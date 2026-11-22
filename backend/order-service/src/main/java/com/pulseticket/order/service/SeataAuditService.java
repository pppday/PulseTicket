package com.pulseticket.order.service;

import com.pulseticket.order.entity.MqEventLog;
import com.pulseticket.order.entity.SeataTxAudit;
import com.pulseticket.order.mapper.MqEventLogMapper;
import com.pulseticket.order.mapper.SeataTxAuditMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class SeataAuditService {

    private final SeataTxAuditMapper seataTxAuditMapper;
    private final MqEventLogMapper mqEventLogMapper;

    /**
     * 使用 REQUIRES_NEW 独立事务记录 Seata AT 全局事务审计轨迹：
     * 即使外层 @GlobalTransactional 因故障注入或抢座冲突回滚，此条审计日志依然保留在数据库中供前端观测！
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW, rollbackFor = Exception.class)
    public void recordSeataAudit(
            String xid,
            String orderNo,
            Long userId,
            Long sessionId,
            String seatIds,
            String txStatus,
            boolean faultInjected,
            String undoSummary,
            long latencyMs
    ) {
        SeataTxAudit audit = new SeataTxAudit();
        audit.setXid(xid != null ? xid : "local-tx-" + System.currentTimeMillis());
        audit.setOrderNo(orderNo);
        audit.setUserId(userId);
        audit.setSessionId(sessionId);
        audit.setSeatIds(seatIds);
        audit.setTxStatus(txStatus);
        audit.setFaultInjected(faultInjected ? 1 : 0);
        audit.setUndoSummary(undoSummary);
        audit.setLatencyMs(latencyMs);
        audit.setCreatedAt(LocalDateTime.now());
        seataTxAuditMapper.insert(audit);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW, rollbackFor = Exception.class)
    public void recordMqEvent(
            String messageId,
            String exchangeName,
            String routingKey,
            String orderNo,
            String eventType,
            String payloadSummary,
            String status
    ) {
        try {
            MqEventLog log = new MqEventLog();
            log.setMessageId(messageId);
            log.setExchangeName(exchangeName);
            log.setRoutingKey(routingKey);
            log.setOrderNo(orderNo);
            log.setEventType(eventType);
            log.setPayloadSummary(payloadSummary);
            log.setStatus(status);
            log.setCreatedAt(LocalDateTime.now());
            log.setUpdatedAt(LocalDateTime.now());
            mqEventLogMapper.insert(log);
        } catch (Exception ignored) {
        }
    }
}
