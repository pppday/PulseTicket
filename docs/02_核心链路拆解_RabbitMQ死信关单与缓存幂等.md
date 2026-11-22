# 核心链路精讲 ②：RabbitMQ 死信延迟关单、Fanout 异步积分幂等与 Redis 三防缓存

> 本文档对应 `PulseTicket` 的第二条核心主干链路（**15 分钟超时未支付死信自动关单 → 14:59 支付与 15:00 关单竞态防御 → 支付后 Fanout 广播出票与积分唯一索引幂等 → Redis 缓存三座大山防护**）。

---

## 一、RabbitMQ 延迟关单（TTL + 死信交换机 DLX）与队头阻塞解法

### 1. 代码链路定位
- 配置与消费者：[OrderMqConfigAndListener.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/mq/OrderMqConfigAndListener.java)
- 座位释放消费者：[ShowMqConfigAndListener.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/show-service/src/main/java/com/pulseticket/show/mq/ShowMqConfigAndListener.java)

### 2. 为什么我们声明了 `15m` 和 `fast (15s)` 两个独立的死信缓冲队列？
- **面试经典坑点（队头阻塞 Head-of-Line Blocking）**：RabbitMQ 的普通队列只会检查**队首第一条消息**是否过期。如果把 15 分钟的标准订单和 15 秒的极速闪购/演示订单混塞进同一个队列，排在后面的 15 秒消息会被前面的 15 分钟消息死死堵住出不来！
- **工程解法**：我们在 `OrderMqConfigAndListener` 中按固定延迟档位拆分为两个无消费者的缓冲队列（`ticket.order.delay.15m.queue` 与 `ticket.order.delay.fast.queue`），到期后统一通过 `x-dead-letter-exchange` 路由到 `ticket.order.close.dlx` → `ticket.order.close.queue` 消费关单！

---

## 二、14:59 付款与 15:00 关单并发撞车怎么防？

- **场景**：用户在倒计时最后 1 秒完成支付，同时死信队列的超时关单消息也投递到了消费者。
- **解法（状态机 CAS 原子互斥）**：查看 [TicketOrderMapper.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/mapper/TicketOrderMapper.java)：
  - 支付 SQL：`UPDATE ticket_order SET status = 'PAID', version = version + 1 WHERE order_no = ? AND status = 'UNPAID'`
  - 关单 SQL：`UPDATE ticket_order SET status = 'CLOSED', version = version + 1 WHERE order_no = ? AND status = 'UNPAID'`
  - 两条 SQL 在 MySQL 行锁下严格串行，谁先抢到谁将 `status` 改掉，后到者的 `WHERE status = 'UNPAID'` 匹配行数为 `0`，自动走幂等跳过分支，绝不会发生“收了钱却把单关了”的事故！

---

## 三、支付成功 Fanout 异步解耦与数据库唯一索引消费幂等

- 当用户点击支付成功后，[OrderFacade.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/service/OrderFacade.java) 向 `ticket.order.paid.fanout` 广播一条 `OrderPaidEventDTO`：
  1. **`show-service` 监听队列 `ticket.show.paid.queue`**：执行 `WHERE locked_order_no = ? AND status = 'LOCKED'` 将座位改为 `SOLD` 并删除 Redis 座位缓存；
  2. **`user-service` 监听队列 `ticket.user.paid.queue`**：在 [UserPointsService.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/user-service/src/main/java/com/pulseticket/user/service/UserPointsService.java) 中先向 `points_log` 插入积分流水。由于表中建有 **`UNIQUE KEY uk_order_biz (order_no, biz_type)`**，即使 RabbitMQ 网络抖动重复投递了 5 次相同消息，后续 4 次都会触发 `DuplicateKeyException` 被捕获并直接回传 `basicAck`，保证**积分绝不翻倍发放**！

---

## 四、Redis 热点演出详情缓存「三座大山」防护

- 源码位置：[ShowCacheAndSeatService.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/show-service/src/main/java/com/pulseticket/show/service/ShowCacheAndSeatService.java)
  1. **防缓存穿透**：查询不存在的 `showId`（如 `99999`）时，向 Redis 写入 `"__NULL__"` 哨兵值（TTL `60s`），拦截恶意不存在 ID 攻击；
  2. **防缓存击穿**：缓存未命中时使用 Redisson `RLock` (`lock:cache:show:rebuild:{showId}`) + **Double-Check 双重检查**，只放 1 个线程查 MySQL 重建缓存；
  3. **防缓存雪崩**：过期时间设为 `1800s + random(60~360s)` 随机抖动值；
  4. **Cache-Aside 双写一致性**：锁座、释放、售出均严格遵循 **先更新 MySQL，再删除 Redis 缓存**（并注册 `TransactionSynchronization.afterCompletion` 钩子兜底清理）。

---

## 📝 自测练习题

1. **追问 1**：为什么消费者必须配 `acknowledge-mode: manual`（手动 ACK），而不能用默认的自动 ACK？
   - <details><summary>点击查看参考答案</summary>自动 ACK 模式下，消息刚发出给消费者（业务代码还没跑完甚至抛异常崩溃了），RabbitMQ 就立即把消息删掉了，导致消息丢失。手动 ACK 确保只有当数据库事务真正提交（或幂等判重完成）后才调用 <code>channel.basicAck</code>。</details>
2. **追问 2**：在 `UserPointsService.java` 中，如果把“捕获 `DuplicateKeyException`”换成“先 `SELECT COUNT(*)` 查一下有没有发过积分，没发过再 `INSERT`”，在高并发重复投递下有什么问题？
   - <details><summary>点击查看参考答案</summary>在并发下两个消费线程同时执行 <code>SELECT</code> 都会查出 <code>0</code>（Check-Then-Act 竞态漏洞），最终还是得靠数据库底层的<b>唯一索引 <code>UNIQUE(order_no, biz_type)</code></b> 做原子互斥兜底。</details>
