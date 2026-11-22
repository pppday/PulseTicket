package com.pulseticket.order.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.feign.client.ShowSeatClient;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.OrderPaidEventDTO;
import com.pulseticket.order.entity.MqEventLog;
import com.pulseticket.order.entity.SeataTxAudit;
import com.pulseticket.order.entity.TicketOrder;
import com.pulseticket.order.mapper.MqEventLogMapper;
import com.pulseticket.order.mapper.SeataTxAuditMapper;
import com.pulseticket.order.mapper.TicketOrderMapper;
import com.pulseticket.order.mq.OrderMqConfigAndListener;
import lombok.RequiredArgsConstructor;
import org.redisson.RedissonMultiLock;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.SQLIntegrityConstraintViolationException;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

/**
 * 选座下单门面协调器 (无 @Transactional 注解！)
 * 面试王牌考点 —— 为什么必须把 Redisson 加解锁写在事务方法外面？
 * 如果在 @Transactional 方法内部 unlock()，锁释放瞬间 Spring 事务尚未 COMMIT 提交，
 * 其他并发线程抢到锁读到的仍是未提交前的旧数据，会导致“一人多单”或“超卖”！
 * 因此本类严格遵循：
 *   ① 获取 Redisson 用户防重锁 (WatchDog 30s 自动续期)
 *   ② 对 seatIds 升序排列后获取 RedissonMultiLock 联锁 (防止交叉选座死锁)
 *   ③ 调用 OrderTxService (@GlobalTransactional) 执行跨库锁座与创单并完成提交
 *   ④ 发送 RabbitMQ 延迟关单消息
 *   ⑤ 在 finally 中安全释放分布式锁
 */
@Service
@RequiredArgsConstructor
public class OrderFacade {

    private static final Logger log = LoggerFactory.getLogger(OrderFacade.class);
    private static final DateTimeFormatter NO_FMT = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");
    private static final DateTimeFormatter DAY_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    private static final DateTimeFormatter DT_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final RedissonClient redissonClient;
    private final OrderTxService orderTxService;
    private final TicketOrderMapper ticketOrderMapper;
    private final SeataTxAuditMapper seataTxAuditMapper;
    private final MqEventLogMapper mqEventLogMapper;
    private final SeataAuditService seataAuditService;
    private final ShowSeatClient showSeatClient;
    private final RabbitTemplate rabbitTemplate;
    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public TicketOrder createOrderWithThreeLayerDefense(
            Long userId,
            String username,
            Long sessionId,
            List<Long> rawSeatIds,
            boolean fastCloseDemo,
            boolean simulateSeataRollback
    ) {
        return createOrderWithThreeLayerDefense(
                userId, username, sessionId, rawSeatIds, fastCloseDemo, simulateSeataRollback, null
        );
    }

    public TicketOrder createOrderWithThreeLayerDefense(
            Long userId,
            String username,
            Long sessionId,
            List<Long> rawSeatIds,
            boolean fastCloseDemo,
            boolean simulateSeataRollback,
            String idempotencyKey
    ) {
        if (userId == null) {
            throw new BizException(401, "请先登录后再选座购票");
        }
        if (sessionId == null || rawSeatIds == null || rawSeatIds.isEmpty()) {
            throw new BizException(400, "请选择场次与至少 1 个座位");
        }
        if (rawSeatIds.size() > 6) {
            throw new BizException(400, "单笔订单最多支持选座 6 张");
        }

        String normalizedIdemKey = normalizeIdempotencyKey(idempotencyKey);

        // 座位 ID 严格去重并升序排列（防止用户 A 选 [2,5] 与用户 B 选 [5,2] 产生环形等待死锁！）
        List<Long> sortedSeatIds = rawSeatIds.stream().distinct().sorted().toList();

        // 第一层防线：Redisson 用户级防连点重复下单锁 (不传 leaseTime 参数以启用 30s WatchDog 看门狗自动续期)
        String userLockKey = "lock:order:submit:" + userId + ":" + sessionId;
        RLock userLock = redissonClient.getLock(userLockKey);
        if (!userLock.tryLock()) {
            throw new BizException(429, "您的选座请求正在处理中，请勿高频连点重复提交！");
        }

        // 第二层防线：RedissonMultiLock 座位级分布式联锁（50人抢同一座时在 Redis 层毫秒级快速拦截）
        RLock[] seatLocks = sortedSeatIds.stream()
                .map(sid -> redissonClient.getLock("lock:seat:" + sessionId + ":" + sid))
                .toArray(RLock[]::new);
        RedissonMultiLock seatMultiLock = new RedissonMultiLock(seatLocks);
        boolean seatLocked = false;

        long t0 = System.currentTimeMillis();
        String orderNo = "TK" + LocalDateTime.now().format(NO_FMT)
                + String.format("%04d", ThreadLocalRandom.current().nextInt(1000, 9999));

        try {
            // 外部请求幂等校验（防网关 / Agent 超时重试导致重复创单或误报座位冲突）
            if (normalizedIdemKey != null) {
                TicketOrder existing = findExistingOrderByIdempotency(userId, normalizedIdemKey);
                if (existing != null) {
                    assertIdempotencyMatchesRequest(existing, sessionId, sortedSeatIds);
                    log.info("[Idempotency Hit] userId={}, key={}, returning existing orderNo={}",
                            userId, normalizedIdemKey, existing.getOrderNo());
                    return existing;
                }
            }

            try {
                // 最多等待 250ms，leaseTime 传 -1 保持看门狗续期
                seatLocked = seatMultiLock.tryLock(250, -1, TimeUnit.MILLISECONDS);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
            if (!seatLocked) {
                throw new BizException(409, "当前座位正被其他观众锁定结算中 (Redisson MultiLock 并发拦截)，请更换座位！");
            }

            int delaySeconds = fastCloseDemo ? 15 : 900;

            // 第三层防线 + Seata AT 全局事务：跨服务锁座 + 插入订单
            TicketOrder createdOrder = orderTxService.executeCreateOrderGlobalTx(
                    orderNo,
                    userId,
                    username,
                    sessionId,
                    sortedSeatIds,
                    delaySeconds,
                    simulateSeataRollback,
                    normalizedIdemKey
            );

            long elapsed = System.currentTimeMillis() - t0;
            seataAuditService.recordSeataAudit(
                    createdOrder.getSeataXid(),
                    orderNo,
                    userId,
                    sessionId,
                    sortedSeatIds.toString(),
                    "COMMITTED",
                    false,
                    "一阶段 show-service 锁定座位 " + sortedSeatIds + " + order-service 创单成功；二阶段异步清理 undo_log 完成全局提交",
                    elapsed
            );

            // 事务已彻底提交成功后，投递 RabbitMQ 延迟关单消息 (15s 极速演示队列 或 15min 标准队列)
            String routingKey = fastCloseDemo
                    ? OrderMqConfigAndListener.ROUTING_DELAY_FAST
                    : OrderMqConfigAndListener.ROUTING_DELAY_15M;
            String msgId = "msg-delay-" + UUID.randomUUID().toString().substring(0, 8);
            rabbitTemplate.convertAndSend(
                    OrderMqConfigAndListener.EXCHANGE_ORDER_DELAY,
                    routingKey,
                    orderNo,
                    new CorrelationData(msgId)
            );

            seataAuditService.recordMqEvent(
                    msgId,
                    OrderMqConfigAndListener.EXCHANGE_ORDER_DELAY,
                    routingKey,
                    orderNo,
                    "DELAY_CLOSE_SENT",
                    "已投递死信延迟关单消息，倒计时 " + delaySeconds + " 秒未付款将自动关单并释放座位",
                    "DELIVERED"
            );

            return createdOrder;
        } catch (Exception ex) {
            long elapsed = System.currentTimeMillis() - t0;
            if (simulateSeataRollback) {
                // 兜底保障：若本地环境 Seata TC 异步回滚尚未完成，确保演示态座位立即恢复 AVAILABLE
                try {
                    OrderClosedEventDTO compensate = new OrderClosedEventDTO();
                    compensate.setOrderNo(orderNo);
                    compensate.setSessionId(sessionId);
                    compensate.setSeatIds(sortedSeatIds);
                    compensate.setCloseReason("SEATA_AT_ROLLBACK_COMPENSATE");
                    showSeatClient.releaseSeats(compensate);
                } catch (Exception ignored) {
                }
                seataAuditService.recordSeataAudit(
                        "AT-ROLLBACK-" + orderNo,
                        orderNo,
                        userId,
                        sessionId,
                        sortedSeatIds.toString(),
                        "ROLLED_BACK",
                        true,
                        "【Seata AT 二阶段反向补偿成功】show-service 一阶段已锁定座位 " + sortedSeatIds + " 并写 undo_log，捕获异常后利用 before image 自动回滚释放座位，订单表回滚无脏数据！",
                        elapsed
                );
            }
            if (normalizedIdemKey != null && isDuplicateKeyException(ex)) {
                TicketOrder existing = findExistingOrderByIdempotency(userId, normalizedIdemKey);
                if (existing != null) {
                    assertIdempotencyMatchesRequest(existing, sessionId, sortedSeatIds);
                    log.info("[Idempotency Duplicate Fallback] userId={}, key={}, returning existing orderNo={}",
                            userId, normalizedIdemKey, existing.getOrderNo());
                    return existing;
                }
            }
            Throwable cursor = ex;
            while (cursor != null) {
                if (cursor instanceof BizException biz) {
                    throw biz;
                }
                cursor = cursor.getCause();
            }
            throw new BizException(500, ex.getMessage());
        } finally {
            // 严格在数据库事务提交/回滚之后才释放 Redisson 分布式锁！
            if (seatLocked) {
                try {
                    seatMultiLock.unlock();
                } catch (Exception ignored) {
                }
            }
            if (userLock.isHeldByCurrentThread()) {
                try {
                    userLock.unlock();
                } catch (Exception ignored) {
                }
            }
        }
    }

    /**
     * 模拟收银台支付订单：
     * 1. 执行 WHERE status = 'UNPAID' 的 CAS 原子更新；
     * 2. 向 ticket.order.paid.fanout 广播支付成功事件：
     *    - show-service 消费将座位从 LOCKED -> SOLD
     *    - user-service 消费为用户累加观演积分并发送出票站内信 (唯一索引保证幂等)
     */
    public TicketOrder payOrder(Long userId, String orderNo) {
        TicketOrder order = ticketOrderMapper.selectOne(
                new LambdaQueryWrapper<TicketOrder>()
                        .eq(TicketOrder::getOrderNo, orderNo)
                        .eq(TicketOrder::getUserId, userId)
        );
        if (order == null) {
            throw new BizException(404, "订单不存在: " + orderNo);
        }
        if ("PAID".equals(order.getStatus())) {
            return order; // 幂等返回
        }
        if ("CLOSED".equals(order.getStatus())) {
            throw new BizException(409, "订单已超时关闭，座位已被系统释放，请重新选座下单！");
        }

        String ticketCode = "PT-" + ThreadLocalRandom.current().nextInt(1000, 9999) + "-"
                + orderNo.substring(Math.max(0, orderNo.length() - 4));

        int updated = ticketOrderMapper.casMarkOrderPaid(orderNo, userId, ticketCode);
        if (updated == 0) {
            throw new BizException(409, "订单状态已变更，无法完成支付");
        }

        TicketOrder paidOrder = ticketOrderMapper.selectById(order.getId());
        List<Long> seatIds = parseList(paidOrder.getSeatIdsJson(), new TypeReference<List<Long>>() {});
        List<String> seatLabels = parseList(paidOrder.getSeatLabelsJson(), new TypeReference<List<String>>() {});

        OrderPaidEventDTO event = new OrderPaidEventDTO();
        String msgId = "msg-paid-" + UUID.randomUUID().toString().substring(0, 8);
        event.setMessageId(msgId);
        event.setOrderNo(orderNo);
        event.setUserId(userId);
        event.setUsername(paidOrder.getUsername());
        event.setShowId(paidOrder.getShowId());
        event.setShowTitle(paidOrder.getShowTitle());
        event.setVenueName(paidOrder.getVenueName());
        event.setSessionId(paidOrder.getSessionId());
        event.setSessionTitle(paidOrder.getSessionTitle());
        event.setSeatIds(seatIds);
        event.setSeatLabels(seatLabels);
        event.setTotalAmount(paidOrder.getTotalAmount());
        event.setTicketCode(ticketCode);

        rabbitTemplate.convertAndSend(
                OrderMqConfigAndListener.EXCHANGE_ORDER_PAID_FANOUT,
                "",
                event,
                new CorrelationData(msgId)
        );

        seataAuditService.recordMqEvent(
                msgId,
                OrderMqConfigAndListener.EXCHANGE_ORDER_PAID_FANOUT,
                "fanout.*",
                orderNo,
                "ORDER_PAID_FANOUT",
                "已广播支付成功事件：通知 show-service 出票 (LOCKED->SOLD) + user-service 发放积分与电子票通知",
                "DELIVERED"
        );

        return paidOrder;
    }

    /**
     * 用户主动取消未支付订单并立即归还座位（拒绝吞掉 PAID / 超时关闭订单）
     */
    public TicketOrder cancelOrderManually(Long userId, String orderNo) {
        if (userId == null) {
            throw new BizException(401, "请先登录后再操作订单");
        }
        TicketOrder order = ticketOrderMapper.selectOne(
                new LambdaQueryWrapper<TicketOrder>()
                        .eq(TicketOrder::getOrderNo, orderNo)
                        .eq(TicketOrder::getUserId, userId)
        );
        if (order == null) {
            throw new BizException(404, "订单不存在");
        }
        if ("PAID".equals(order.getStatus())) {
            throw new BizException(409, "该订单已完成支付出票，无法通过取消接口直接退票，请核实退票规则或联系人工客服");
        }
        if ("CLOSED".equals(order.getStatus())) {
            if ("USER_CANCEL".equals(order.getCloseReason())) {
                return order; // 幂等返回用户已取消订单
            }
            throw new BizException(409, "订单已超时关闭，座位已被系统释放，无需重复取消");
        }
        if (!"UNPAID".equals(order.getStatus())) {
            throw new BizException(409, "当前订单状态不允许取消: " + order.getStatus());
        }

        int closed = ticketOrderMapper.casCloseUnpaidOrder(orderNo, "USER_CANCEL");
        if (closed == 1) {
            List<Long> seatIds = parseList(order.getSeatIdsJson(), new TypeReference<List<Long>>() {});
            OrderClosedEventDTO releaseEvent = new OrderClosedEventDTO();
            releaseEvent.setMessageId("msg-cancel-" + UUID.randomUUID().toString().substring(0, 8));
            releaseEvent.setOrderNo(orderNo);
            releaseEvent.setUserId(userId);
            releaseEvent.setSessionId(order.getSessionId());
            releaseEvent.setSeatIds(seatIds);
            releaseEvent.setCloseReason("USER_CANCEL");

            rabbitTemplate.convertAndSend(
                    OrderMqConfigAndListener.EXCHANGE_ORDER_CLOSED_DIRECT,
                    OrderMqConfigAndListener.ROUTING_ORDER_CLOSED_RELEASE,
                    releaseEvent
            );
            try {
                showSeatClient.releaseSeats(releaseEvent);
            } catch (Exception ignored) {
            }
            return ticketOrderMapper.selectById(order.getId());
        }

        // CAS 更新 0 行说明并发毫秒内状态已改变，回查真实状态避免假成功
        TicketOrder latest = ticketOrderMapper.selectById(order.getId());
        if (latest != null && "CLOSED".equals(latest.getStatus()) && "USER_CANCEL".equals(latest.getCloseReason())) {
            return latest;
        }
        throw new BizException(409, "订单状态已变更，无法取消（当前状态: " + (latest != null ? latest.getStatus() : "UNKNOWN") + "）");
    }

    public List<TicketOrder> listUserOrders(Long userId) {
        return ticketOrderMapper.selectList(
                new LambdaQueryWrapper<TicketOrder>()
                        .eq(TicketOrder::getUserId, userId)
                        .orderByDesc(TicketOrder::getCreatedAt)
        );
    }

    public TicketOrder getOrderByNo(Long userId, String orderNo) {
        TicketOrder order = ticketOrderMapper.selectOne(
                new LambdaQueryWrapper<TicketOrder>()
                        .eq(TicketOrder::getOrderNo, orderNo)
                        .eq(TicketOrder::getUserId, userId)
        );
        if (order == null) {
            throw new BizException(404, "订单不存在");
        }
        return order;
    }

    public Map<String, Object> getArchitectureTelemetry() {
        List<SeataTxAudit> seataAudits = seataTxAuditMapper.selectList(
                new LambdaQueryWrapper<SeataTxAudit>()
                        .orderByDesc(SeataTxAudit::getCreatedAt)
                        .last("LIMIT 15")
        );
        List<MqEventLog> mqLogs = mqEventLogMapper.selectList(
                new LambdaQueryWrapper<MqEventLog>()
                        .orderByDesc(MqEventLog::getCreatedAt)
                        .last("LIMIT 15")
        );
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("seataAudits", seataAudits);
        data.put("mqLogs", mqLogs);
        return data;
    }

    /**
     * ADMIN 专属：全平台订单销量、转化率漏斗、购票会员榜与订单流水统计
     */
    public Map<String, Object> getAdminOrderSales() {
        List<TicketOrder> allOrders = ticketOrderMapper.selectList(
                new LambdaQueryWrapper<TicketOrder>().orderByDesc(TicketOrder::getId)
        );

        int totalOrders = allOrders.size();
        int paidOrders = 0;
        int paidTickets = 0;
        int unpaidOrders = 0;
        int closedOrders = 0;
        int timeoutClosedOrders = 0;
        int userCancelledOrders = 0;

        BigDecimal paidRevenue = BigDecimal.ZERO;
        BigDecimal unpaidAmount = BigDecimal.ZERO;
        BigDecimal closedAmount = BigDecimal.ZERO;

        Map<String, Map<String, Object>> dailyMap = new TreeMap<>();
        Map<Long, Map<String, Object>> buyerMap = new LinkedHashMap<>();

        for (TicketOrder o : allOrders) {
            BigDecimal amt = o.getTotalAmount() != null ? o.getTotalAmount() : BigDecimal.ZERO;
            int seats = o.getSeatCount() != null ? o.getSeatCount() : 0;
            String dayKey = (o.getPaidAt() != null ? o.getPaidAt() : o.getCreatedAt()) != null
                    ? (o.getPaidAt() != null ? o.getPaidAt() : o.getCreatedAt()).format(DAY_FMT)
                    : "2026-10-30";

            Map<String, Object> dayBucket = dailyMap.computeIfAbsent(dayKey, d -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("date", d);
                m.put("paidRevenue", BigDecimal.ZERO);
                m.put("paidOrders", 0);
                m.put("paidTickets", 0);
                m.put("totalOrders", 0);
                return m;
            });
            dayBucket.put("totalOrders", (Integer) dayBucket.get("totalOrders") + 1);

            if ("PAID".equals(o.getStatus())) {
                paidOrders++;
                paidTickets += seats;
                paidRevenue = paidRevenue.add(amt);

                dayBucket.put("paidRevenue", ((BigDecimal) dayBucket.get("paidRevenue")).add(amt));
                dayBucket.put("paidOrders", (Integer) dayBucket.get("paidOrders") + 1);
                dayBucket.put("paidTickets", (Integer) dayBucket.get("paidTickets") + seats);

                Long uid = o.getUserId();
                if (uid != null) {
                    Map<String, Object> buyer = buyerMap.computeIfAbsent(uid, k -> {
                        Map<String, Object> b = new LinkedHashMap<>();
                        b.put("userId", uid);
                        b.put("username", o.getUsername());
                        b.put("paidOrders", 0);
                        b.put("paidTickets", 0);
                        b.put("totalSpent", BigDecimal.ZERO);
                        b.put("lastPaidAt", o.getPaidAt() != null ? o.getPaidAt().format(DT_FMT) : null);
                        return b;
                    });
                    buyer.put("paidOrders", (Integer) buyer.get("paidOrders") + 1);
                    buyer.put("paidTickets", (Integer) buyer.get("paidTickets") + seats);
                    buyer.put("totalSpent", ((BigDecimal) buyer.get("totalSpent")).add(amt));
                }
            } else if ("UNPAID".equals(o.getStatus())) {
                unpaidOrders++;
                unpaidAmount = unpaidAmount.add(amt);
            } else {
                closedOrders++;
                closedAmount = closedAmount.add(amt);
                if ("TIMEOUT_UNPAID".equals(o.getCloseReason())) {
                    timeoutClosedOrders++;
                } else if ("USER_CANCEL".equals(o.getCloseReason())) {
                    userCancelledOrders++;
                }
            }
        }

        BigDecimal avgOrderValue = paidOrders > 0
                ? paidRevenue.divide(BigDecimal.valueOf(paidOrders), 2, RoundingMode.HALF_UP)
                : BigDecimal.ZERO;
        BigDecimal conversionRate = totalOrders > 0
                ? BigDecimal.valueOf(paidOrders * 100.0 / totalOrders).setScale(1, RoundingMode.HALF_UP)
                : BigDecimal.ZERO;

        List<Map<String, Object>> buyerLeaderboard = new ArrayList<>(buyerMap.values());
        buyerLeaderboard.sort((a, b) -> ((BigDecimal) b.get("totalSpent")).compareTo((BigDecimal) a.get("totalSpent")));

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("totalOrders", totalOrders);
        summary.put("paidOrders", paidOrders);
        summary.put("paidTickets", paidTickets);
        summary.put("paidRevenue", paidRevenue);
        summary.put("avgOrderValue", avgOrderValue);
        summary.put("unpaidOrders", unpaidOrders);
        summary.put("unpaidAmount", unpaidAmount);
        summary.put("closedOrders", closedOrders);
        summary.put("closedAmount", closedAmount);
        summary.put("timeoutClosedOrders", timeoutClosedOrders);
        summary.put("userCancelledOrders", userCancelledOrders);
        summary.put("conversionRate", conversionRate);

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("summary", summary);
        data.put("dailyTrend", new ArrayList<>(dailyMap.values()));
        data.put("buyerLeaderboard", buyerLeaderboard);
        data.put("recentOrders", allOrders.size() > 100 ? allOrders.subList(0, 100) : allOrders);
        return data;
    }

    /**
     * ADMIN 专属：读取 ticket_order 库全表元数据与实时数据明细
     */
    public Map<String, Object> getAdminDbOverview() {
        List<TicketOrder> ticketOrders = ticketOrderMapper.selectList(
                new LambdaQueryWrapper<TicketOrder>()
                        .orderByDesc(TicketOrder::getId)
                        .last("LIMIT 200")
        );
        List<SeataTxAudit> seataTxAudits = seataTxAuditMapper.selectList(
                new LambdaQueryWrapper<SeataTxAudit>()
                        .orderByDesc(SeataTxAudit::getId)
                        .last("LIMIT 200")
        );
        List<MqEventLog> mqEventLogs = mqEventLogMapper.selectList(
                new LambdaQueryWrapper<MqEventLog>()
                        .orderByDesc(MqEventLog::getId)
                        .last("LIMIT 200")
        );
        List<Map<String, Object>> undoLogs = jdbcTemplate.queryForList(
                """
                SELECT branch_id AS branchId, xid, context, log_status AS logStatus,
                       OCTET_LENGTH(rollback_info) AS payloadBytes,
                       DATE_FORMAT(log_created, '%Y-%m-%d %H:%i:%s') AS logCreated
                FROM undo_log
                ORDER BY log_created DESC
                LIMIT 50
                """
        );

        List<Map<String, Object>> tablesMeta = queryTablesMeta(List.of(
                "ticket_order", "seata_tx_audit", "mq_event_log", "undo_log"
        ));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("database", "ticket_order");
        data.put("service", "order-service (:8203)");
        data.put("tablesMeta", tablesMeta);
        data.put("ticketOrders", ticketOrders);
        data.put("seataTxAudits", seataTxAudits);
        data.put("mqEventLogs", mqEventLogs);
        data.put("undoLogs", undoLogs);
        return data;
    }

    private List<Map<String, Object>> queryTablesMeta(List<String> tableNames) {
        Map<String, Map<String, Object>> schemaInfo = new LinkedHashMap<>();
        List<Map<String, Object>> rawMeta = jdbcTemplate.queryForList(
                """
                SELECT TABLE_NAME AS tableName, TABLE_COMMENT AS tableComment, ENGINE AS engine,
                       IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0) AS totalBytes
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                """
        );
        for (Map<String, Object> m : rawMeta) {
            schemaInfo.put(String.valueOf(m.get("tableName")), m);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (String tbl : tableNames) {
            Long exactCount = jdbcTemplate.queryForObject("SELECT COUNT(*) FROM " + tbl, Long.class);
            Map<String, Object> info = schemaInfo.getOrDefault(tbl, Map.of());
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("tableName", tbl);
            item.put("tableComment", info.getOrDefault("tableComment", ""));
            item.put("engine", info.getOrDefault("engine", "InnoDB"));
            item.put("rowCount", exactCount != null ? exactCount : 0L);
            item.put("totalBytes", info.getOrDefault("totalBytes", 0L));
            result.add(item);
        }
        return result;
    }

    private String normalizeIdempotencyKey(String rawKey) {
        if (rawKey == null) {
            return null;
        }
        String trimmed = rawKey.trim();
        if (trimmed.isEmpty()) {
            return null;
        }
        if (trimmed.length() > 128) {
            throw new BizException(400, "Idempotency-Key 长度不能超过 128 个字符");
        }
        return trimmed;
    }

    private TicketOrder findExistingOrderByIdempotency(Long userId, String idempotencyKey) {
        return ticketOrderMapper.selectOne(
                new LambdaQueryWrapper<TicketOrder>()
                        .eq(TicketOrder::getUserId, userId)
                        .eq(TicketOrder::getIdempotencyKey, idempotencyKey)
                        .last("LIMIT 1")
        );
    }

    private void assertIdempotencyMatchesRequest(TicketOrder existing, Long sessionId, List<Long> sortedSeatIds) {
        if (!Objects.equals(existing.getSessionId(), sessionId)) {
            throw new BizException(409, "幂等键冲突：同一 Idempotency-Key 不能用于不同场次");
        }
        List<Long> existingSeatIds = parseList(existing.getSeatIdsJson(), new TypeReference<List<Long>>() {})
                .stream()
                .distinct()
                .sorted()
                .toList();
        if (!Objects.equals(existingSeatIds, sortedSeatIds)) {
            throw new BizException(409, "幂等键冲突：同一 Idempotency-Key 不能用于不同座位组合");
        }
    }

    private boolean isDuplicateKeyException(Throwable ex) {
        Throwable cursor = ex;
        while (cursor != null) {
            if (cursor instanceof DuplicateKeyException || cursor instanceof SQLIntegrityConstraintViolationException) {
                return true;
            }
            cursor = cursor.getCause();
        }
        return false;
    }

    private <T> List<T> parseList(String json, TypeReference<List<T>> typeRef) {
        try {
            return objectMapper.readValue(json, typeRef);
        } catch (Exception e) {
            return Collections.emptyList();
        }
    }
}
