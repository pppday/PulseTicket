# 核心链路精讲 ①：网关登录态透传、Redisson 三层锁防超卖与 Seata AT 分布式事务

> 本文档对应 `PulseTicket` 的第一条核心主干链路（**登录验签 → 网关透传 → 选座抢票防重防超卖 → 跨库分布式事务锁座与创单**）。每节均附有**对应源码位置、面试标准回答话术与自测思考题**。

---

## 一、全链路登录态三棒接力（Gateway + ThreadLocal + OpenFeign）

### 1. 代码链路定位
1. **第一棒（网关统一验签与防伪造注入）**：[AuthGlobalFilter.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/ticket-gateway/src/main/java/com/pulseticket/gateway/filter/AuthGlobalFilter.java)
   - 拦截外部对 `/internal/**` 的直接访问（返回 `403`）；
   - 校验 Bearer JWT 签名；
   - **先调用 `headers.remove("X-User-Id")` 清除客户端可能伪造的请求头，再调用 `headers.set("X-User-Id", userId)` 注入真实用户 ID**。
2. **第二棒（下游微服务写入与清理 `ThreadLocal`）**：[UserInfoInterceptor.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/ticket-common/src/main/java/com/pulseticket/common/interceptor/UserInfoInterceptor.java) & [UserContext.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/ticket-common/src/main/java/com/pulseticket/common/context/UserContext.java)
   - `preHandle()`：提取 `X-User-Id` 存入 `UserContext.setUserId(...)`；
   - **`afterCompletion()`**：无条件执行 `UserContext.clear()`（底层调用 `ThreadLocal.remove()`），防止 Tomcat 线程池复用导致内存泄漏与用户串号！
3. **第三棒（OpenFeign 跨服务继续透传）**：[FeignRelayAutoConfig.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/ticket-feign-api/src/main/java/com/pulseticket/feign/config/FeignRelayAutoConfig.java)
   - 实现 `feign.RequestInterceptor`，从 `UserContext` 取出 `userId`、`traceId` 与 Seata `TX_XID` 塞进 Feign 新请求头，传递给 `show-service`。

---

## 二、选座下单「三层并发防线」与“锁包事务”铁律

### 1. 为什么拆成 [OrderFacade.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/service/OrderFacade.java) 和 [OrderTxService.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/service/OrderTxService.java) 两个类？
- **大坑复盘**：如果把 Redisson `lock.lock()` 和 `lock.unlock()` 写在带有 `@Transactional` 的方法内部，当方法执行到 `finally { lock.unlock(); }` 时，**锁已经释放了，但 Spring 的数据库事务要等退出代理方法后才真正 `COMMIT`**！在这几毫秒空窗期冲进来的第二个线程抢到锁查到的仍是未提交旧数据，直接导致重复下单或超卖。
- **正确解法**：外层 `OrderFacade` **不加事务注解**，专门负责：
  1. **第 1 层（Redisson 用户级防连点锁）**：`lock:order:submit:{userId}:{sessionId}`，`tryLock()` 不传 `leaseTime` 以开启 **30s WatchDog 看门狗自动续期**，拦截用户手抖连点（返回 `429`）；
  2. **第 2 层（RedissonMultiLock 座位联锁 + ID 升序防死锁）**：先把所选 `seatIds` **升序排序**（防止用户 A 选 `[2,5]`、用户 B 选 `[5,2]` 产生交叉等待死锁），再加 `RedissonMultiLock`，50 人同时抢同一座时在 Redis 内存层毫秒级快速失败（返回 `409`）；
  3. 调用内层 `OrderTxService.executeCreateOrderGlobalTx(...)` 完成事务提交；
  4. 最后在外层 `finally` 释放锁！

---

## 三、Seata AT 跨服务分布式事务与两大实战陷阱

### 1. Seata AT 一阶段与二阶段在项目里干了什么？
- **一阶段**：
  - `order-service`（TM）开启 `@GlobalTransactional` 生成全局 `XID`；
  - 通过 Feign 调用 `show-service`（RM）执行 [SeatInventoryMapper.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/show-service/src/main/java/com/pulseticket/show/mapper/SeatInventoryMapper.java) 的 `WHERE status = 'AVAILABLE'` 条件更新（**第 3 层数据库兜底防线**），Seata 代理数据源自动保存修改前镜像（`before image`：`status='AVAILABLE'`）和修改后镜像（`after image`：`status='LOCKED'`）写入 `ticket_show.undo_log` 表并提交本地事务；
  - `order-service` 向 `ticket_order` 插入订单并写 `undo_log`。
- **二阶段**：
  - **正常提交**：异步删除两库的 `undo_log` 行；
  - **异常回滚（可在前端勾选「Seata 故障回滚注入」亲眼验证）**：`order-service` 抛出异常后，Seata TC 驱动 `show-service` 比对 `after image` 无脏写后，用 `before image` 反向执行 SQL 将座位恢复为 `AVAILABLE`！

### 2. Sentinel / Feign 降级吞异常陷阱
- 在 [ShowSeatClientFallbackFactory.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/ticket-feign-api/src/main/java/com/pulseticket/feign/fallback/ShowSeatClientFallbackFactory.java) 中，当下游熔断降级时返回的是 `Result.fail(503, ...)` 正常 Java 对象而非抛异常。
- 因此在 [OrderTxService.java](file:///c:/Users/lan/Desktop/项目/pulseticket/backend/order-service/src/main/java/com/pulseticket/order/service/OrderTxService.java) 第 72 行**必须显式判断 `if (!lockRes.isSuccess()) throw new BizException(...)`**，否则 `@GlobalTransactional` 感知不到异常就不会回滚！

---

## 📝 自测练习题（检验你能不能扛住面试官追问）

1. **追问 1**：如果用户 A 选了座位 `[6, 2]`，用户 B 同一毫秒选了座位 `[2, 6]`，不做处理直接按顺序循环加锁会发生什么？我们在 `OrderFacade.java` 第 85 行是怎么用一行代码解决的？
   - <details><summary>点击查看参考答案</summary>会发生经典的 <b>AB-BA 环形等待死锁</b>（A 拿着 6 等 2，B 拿着 2 等 6）。我们在加锁前执行了 <code>rawSeatIds.stream().distinct().sorted().toList()</code>，强制所有线程都按座位 ID <b>从小到大（先 2 后 6）</b> 的顺序申请锁，破坏了死锁四大条件中的“循环等待条件”。</details>
2. **追问 2**：调用 Redisson 的 `lock.tryLock(250, 10000, TimeUnit.MILLISECONDS)` 和 `lock.tryLock(250, -1, TimeUnit.MILLISECONDS)` 有什么本质区别？
   - <details><summary>点击查看参考答案</summary>传了具体正数 <code>10000</code>（10秒租期）会<b>直接禁用 WatchDog 看门狗自动续期</b>；传 <code>-1</code>（或调用无租期参数的 <code>tryLock()</code>）才会开启默认 30s 租期、每 10s 自动续期的看门狗机制。</details>
3. **追问 3**：为什么记录 Seata 回滚轨迹的 `SeataAuditService.recordSeataAudit()` 必须标 `@Transactional(propagation = Propagation.REQUIRES_NEW)`？
   - <details><summary>点击查看参考答案</summary>如果使用默认的 <code>REQUIRED</code>，审计日志会加入外层正在回滚的事务一起被回滚掉，数据库里就查不到失败回滚记录了；<code>REQUIRES_NEW</code> 会挂起外层事务、开启一条全新的独立数据库连接提交审计日志。</details>
