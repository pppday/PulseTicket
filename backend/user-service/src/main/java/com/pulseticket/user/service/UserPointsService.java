package com.pulseticket.user.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.pulseticket.feign.dto.OrderPaidEventDTO;
import com.pulseticket.feign.dto.OrderRefundedEventDTO;
import com.pulseticket.user.entity.PointsLog;
import com.pulseticket.user.entity.UserAccount;
import com.pulseticket.user.entity.UserMessage;
import com.pulseticket.user.mapper.PointsLogMapper;
import com.pulseticket.user.mapper.UserAccountMapper;
import com.pulseticket.user.mapper.UserMessageMapper;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 会员观演积分与站内信异步补偿服务。
 * 通过 `points_log` 联合唯一索引 `(order_no, biz_type)` 兜底，保证 RabbitMQ 消息重投时既不会多发积分，也不会重复扣分。
 */
@Service
@RequiredArgsConstructor
public class UserPointsService {

    private static final Logger log = LoggerFactory.getLogger(UserPointsService.class);

    private final UserAccountMapper userAccountMapper;
    private final PointsLogMapper pointsLogMapper;
    private final UserMessageMapper userMessageMapper;

    /**
     * 消费 RabbitMQ 订单支付成功消息：发放观演积分 + 签发电子票站内信
     * 面试核心考点：
     * RabbitMQ 是 At-Least-Once (至少投递一次)，网络抖动必然产生重复消息！
     * 靠 points_log 表的 UNIQUE KEY uk_order_biz (order_no, biz_type) 做数据库唯一索引防重幂等：
     * 只有首次插入流水成功才累加积分，若抛 DuplicateKeyException 说明已消费过，直接幂等跳过！
     */
    @Transactional(rollbackFor = Exception.class)
    public boolean processOrderPaidRewardIdempotent(OrderPaidEventDTO event) {
        int rewardPoints = event.getTotalAmount() != null ? event.getTotalAmount().intValue() : 100;
        UserAccount user = userAccountMapper.selectById(event.getUserId());
        int currentBalance = (user != null && user.getPointsBalance() != null) ? user.getPointsBalance() : 0;

        PointsLog logEntry = new PointsLog();
        logEntry.setUserId(event.getUserId());
        logEntry.setOrderNo(event.getOrderNo());
        logEntry.setBizType("ORDER_PAID_REWARD");
        logEntry.setPointsDelta(rewardPoints);
        logEntry.setBalanceAfter(currentBalance + rewardPoints);
        logEntry.setRemark("购票出票奖励积分 · " + event.getShowTitle());
        logEntry.setCreatedAt(LocalDateTime.now());

        try {
            pointsLogMapper.insert(logEntry);
        } catch (DuplicateKeyException dup) {
            log.info("[MQ幂等拦截] 订单 {} 的积分奖励已发放过，忽略重复 MQ 投递", event.getOrderNo());
            return false;
        }

        // 原子累加用户积分与累计购票单数
        userAccountMapper.addPointsAndOrderCount(event.getUserId(), rewardPoints);

        // 发送电子票签发站内信 (同样带 uk_order_msg_type 唯一约束)
        UserMessage msg = new UserMessage();
        msg.setUserId(event.getUserId());
        msg.setOrderNo(event.getOrderNo());
        msg.setMsgType("TICKET_ISSUED");
        msg.setTitle("【出票成功】" + event.getShowTitle());
        String seatsText = (event.getSeatLabels() != null && !event.getSeatLabels().isEmpty())
                ? String.join("、", event.getSeatLabels())
                : "已选座位";
        msg.setContent(String.format(
                "您的电子票已由系统实时签发！场次：%s | 座位：%s | 取票核验码：%s | 已发放 +%d 观演积分。",
                event.getSessionTitle(),
                seatsText,
                event.getTicketCode() != null ? event.getTicketCode() : "PT-2026",
                rewardPoints
        ));
        msg.setIsRead(0);
        msg.setCreatedAt(LocalDateTime.now());
        try {
            userMessageMapper.insert(msg);
        } catch (DuplicateKeyException ignored) {
        }

        log.info("[MQ异步解耦成功] 订单 {} 发放积分 +{} 并签发电子票通知", event.getOrderNo(), rewardPoints);
        return true;
    }

    /**
     * 消费 RabbitMQ 退票成功消息：幂等冲正扣回原单发放的观演积分 + 下发退票站内信
     * - 防止“下单领积分 -> 免手续费退票白嫖积分”的刷分漏洞；
     * - 依靠 points_log 的 UNIQUE KEY uk_order_biz (order_no, biz_type='ORDER_REFUND_DEDUCT') 保证重投绝不重复扣分；
     * - 使用 GREATEST(0, points_balance - deduct) 兜底，避免余额扣成负数。
     */
    @Transactional(rollbackFor = Exception.class)
    public boolean processOrderRefundDeductIdempotent(OrderRefundedEventDTO event) {
        if (event == null || event.getOrderNo() == null || event.getUserId() == null) {
            return false;
        }

        // 优先以该订单实际曾发放的 ORDER_PAID_REWARD 积分为准，避免金额口径偏差
        PointsLog paidLog = pointsLogMapper.selectOne(
                new LambdaQueryWrapper<PointsLog>()
                        .eq(PointsLog::getOrderNo, event.getOrderNo())
                        .eq(PointsLog::getBizType, "ORDER_PAID_REWARD")
                        .last("LIMIT 1")
        );
        int deductPoints;
        if (paidLog != null && paidLog.getPointsDelta() != null && paidLog.getPointsDelta() > 0) {
            deductPoints = paidLog.getPointsDelta();
        } else if (event.getPointsToDeduct() != null && event.getPointsToDeduct() > 0) {
            deductPoints = event.getPointsToDeduct();
        } else if (event.getTotalAmount() != null) {
            deductPoints = Math.max(0, event.getTotalAmount().intValue());
        } else {
            deductPoints = 0;
        }

        UserAccount user = userAccountMapper.selectById(event.getUserId());
        int currentBalance = (user != null && user.getPointsBalance() != null) ? user.getPointsBalance() : 0;
        int balanceAfter = Math.max(0, currentBalance - deductPoints);
        String showTitle = event.getShowTitle() != null ? event.getShowTitle() : "演出订单";

        PointsLog deductLog = new PointsLog();
        deductLog.setUserId(event.getUserId());
        deductLog.setOrderNo(event.getOrderNo());
        deductLog.setBizType("ORDER_REFUND_DEDUCT");
        deductLog.setPointsDelta(-deductPoints);
        deductLog.setBalanceAfter(balanceAfter);
        deductLog.setRemark("阶梯退票积分冲正扣回 · " + showTitle);
        deductLog.setCreatedAt(LocalDateTime.now());

        try {
            pointsLogMapper.insert(deductLog);
        } catch (DuplicateKeyException dup) {
            log.info("[MQ幂等拦截] 订单 {} 的退票积分冲正已执行过，忽略重复 MQ 投递", event.getOrderNo());
            return false;
        }

        userAccountMapper.deductPointsAndOrderCount(event.getUserId(), deductPoints);

        UserMessage msg = new UserMessage();
        msg.setUserId(event.getUserId());
        msg.setOrderNo(event.getOrderNo());
        msg.setMsgType("ORDER_REFUNDED");
        msg.setTitle("【退票办结】" + showTitle);
        msg.setContent(String.format(
                "订单 %s 已按「%s」办结退票：实付 ¥%s，手续费 ¥%s（%s），实退 ¥%s（原路退回）；原发放的 %d 观演积分已同步冲正扣回。",
                event.getOrderNo(),
                event.getTierName() != null ? event.getTierName() : "阶梯退票规则",
                event.getTotalAmount() != null ? event.getTotalAmount() : "0.00",
                event.getFeeAmount() != null ? event.getFeeAmount() : "0.00",
                event.getFeeRate() != null ? event.getFeeRate() : "0%",
                event.getRefundAmount() != null ? event.getRefundAmount() : "0.00",
                deductPoints
        ));
        msg.setIsRead(0);
        msg.setCreatedAt(LocalDateTime.now());
        try {
            userMessageMapper.insert(msg);
        } catch (DuplicateKeyException ignored) {
        }

        log.info("[MQ退票补偿成功] 订单 {} 冲正扣回观演积分 -{} 并下发退票站内信", event.getOrderNo(), deductPoints);
        return true;
    }
}
