package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 订单超时未支付自动关单 (或用户主动取消/退票) 后通知 show-service 释放座位的事件载体
 * 既用于 RabbitMQ 异步事件消息，也用于 Feign 同步调 releaseSeats 接口的参数传输。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderClosedEventDTO implements Serializable {

    /** 消息唯一 ID（用于链路日志追踪与幂等标识） */
    private String messageId;
    /** 被关闭/取消/退票的订单号 */
    private String orderNo;
    /** 下单用户 ID */
    private Long userId;
    /** 关联的演出场次 ID（释座后需据此刷新该场次的 Redis 座位缓存） */
    private Long sessionId;
    /** 需要释放的座位 ID 列表 */
    private List<Long> seatIds;
    /** 关单原因（如 TIMEOUT_AUTO_CLOSE / USER_CANCEL / USER_REFUND） */
    private String closeReason;
}
