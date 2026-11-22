package com.pulseticket.order.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.common.result.Result;
import com.pulseticket.feign.client.ShowSeatClient;
import com.pulseticket.feign.dto.LockSeatsDTO;
import com.pulseticket.feign.dto.SeatLockResultVO;
import com.pulseticket.order.entity.TicketOrder;
import com.pulseticket.order.mapper.TicketOrderMapper;
import io.seata.core.context.RootContext;
import io.seata.spring.annotation.GlobalTransactional;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 内部分布式事务执行器 (由外层无事务的 OrderFacade 在获取 Redisson 分布式锁后调用)
 * 面试核心考点：
 * 1. 为什么拆成 OrderFacade + OrderTxService 两个类？
 *    a) 保证“Redisson 锁范围 > 事务范围”（先提交事务，再在 finally 释放锁，杜绝锁提前释放导致超卖）；
 *    b) 避免同一个类内部 this.method() 自调用导致 Spring AOP 事务代理失效！
 * 2. @GlobalTransactional 开启 Seata AT 全局事务 (TM)：
 *    自动向 TC 注册全局 XID，并通过 Feign Header 透传给 show-service (RM)。
 * 3. Feign 降级陷阱防御：
 *    若 showSeatClient.lockSeats 触发 Sentinel FallbackFactory 返回 Result.fail(503)，
 *    必须显式判断 !lockRes.isSuccess() 并抛出异常，否则 Seata 不会触发回滚！
 */
@Service
@RequiredArgsConstructor
public class OrderTxService {

    private static final Logger log = LoggerFactory.getLogger(OrderTxService.class);
    private static final DateTimeFormatter DT_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final TicketOrderMapper ticketOrderMapper;
    private final ShowSeatClient showSeatClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${pulseticket.fault.after-seat-lock:false}")
    private boolean globalFaultSwitch;

    @GlobalTransactional(name = "pulseticket-create-order-at-tx", rollbackFor = Exception.class)
    @Transactional(rollbackFor = Exception.class)
    public TicketOrder executeCreateOrderGlobalTx(
            String orderNo,
            Long userId,
            String username,
            Long sessionId,
            List<Long> sortedSeatIds,
            int delaySeconds,
            boolean simulateSeataRollback
    ) {
        return executeCreateOrderGlobalTx(
                orderNo, userId, username, sessionId, sortedSeatIds, delaySeconds, simulateSeataRollback, null
        );
    }

    @GlobalTransactional(name = "pulseticket-create-order-at-tx", rollbackFor = Exception.class)
    @Transactional(rollbackFor = Exception.class)
    public TicketOrder executeCreateOrderGlobalTx(
            String orderNo,
            Long userId,
            String username,
            Long sessionId,
            List<Long> sortedSeatIds,
            int delaySeconds,
            boolean simulateSeataRollback,
            String idempotencyKey
    ) {
        String xid = RootContext.getXID();
        if (xid != null) {
            com.pulseticket.common.context.UserContext.setSeataXid(xid);
        }
        log.info("[Seata TM order-service] 开启全局事务 XID={}, orderNo={}, seatIds={}", xid, orderNo, sortedSeatIds);

        // 1. 通过 OpenFeign 同步调用 show-service 锁定座位 (show-service 本地事务一阶段写入 undo_log 并提交)
        LockSeatsDTO lockDto = new LockSeatsDTO(orderNo, userId, sessionId, sortedSeatIds);
        Result<SeatLockResultVO> lockRes = showSeatClient.lockSeats(lockDto);

        // 【面试必考：Feign 降级吞异常陷阱】
        if (lockRes == null || !lockRes.isSuccess() || lockRes.getData() == null) {
            int errCode = lockRes != null ? lockRes.getCode() : 503;
            String errMsg = lockRes != null ? lockRes.getMessage() : "演出座位服务响应为空";
            throw new BizException(errCode, errMsg);
        }

        SeatLockResultVO seatVo = lockRes.getData();

        // 2. 在 ticket_order 库插入待支付订单 (一阶段同样生成 undo_log 前后镜像)
        TicketOrder order = new TicketOrder();
        order.setOrderNo(orderNo);
        order.setIdempotencyKey(idempotencyKey);
        order.setUserId(userId);
        order.setUsername(username);
        order.setShowId(seatVo.getShowId());
        order.setShowTitle(seatVo.getShowTitle());
        order.setVenueName(seatVo.getVenueName());
        order.setSessionId(seatVo.getSessionId());
        order.setSessionTitle(seatVo.getSessionTitle());
        order.setStartTime(LocalDateTime.parse(seatVo.getStartTime(), DT_FMT));
        try {
            order.setSeatIdsJson(objectMapper.writeValueAsString(seatVo.getSeatIds()));
            order.setSeatLabelsJson(objectMapper.writeValueAsString(seatVo.getSeatLabels()));
        } catch (Exception e) {
            order.setSeatIdsJson(sortedSeatIds.toString());
            order.setSeatLabelsJson(seatVo.getSeatLabels().toString());
        }
        order.setSeatCount(sortedSeatIds.size());
        order.setTotalAmount(seatVo.getTotalAmount());
        order.setStatus("UNPAID");
        order.setDelaySeconds(delaySeconds);
        order.setSeataXid(xid != null ? xid : "AT-" + orderNo);
        LocalDateTime now = LocalDateTime.now();
        order.setExpireAt(now.plusSeconds(delaySeconds));
        order.setVersion(0);
        order.setCreatedAt(now);
        order.setUpdatedAt(now);

        ticketOrderMapper.insert(order);

        // 3. 【Seata AT 故障注入开关】：用于直观演示“show-service 一阶段已锁座落盘，order-service 抛异常触发二阶段 undo_log 自动回滚释放座位”
        if (simulateSeataRollback || globalFaultSwitch) {
            log.warn("[Seata AT 故障演练触发] orderNo={}, XID={} 主动抛出异常以触发跨库二阶段回滚！", orderNo, xid);
            throw new BizException(500, "【Seata AT 故障注入演练】show-service 一阶段已锁座落库并记录 undo_log，order-service 模拟异常触发二阶段反向补偿回滚，座位已恢复 AVAILABLE！");
        }

        return order;
    }
}
