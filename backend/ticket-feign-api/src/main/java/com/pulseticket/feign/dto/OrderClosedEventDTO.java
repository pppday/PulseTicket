package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 订单超时未支付自动关单 (或用户主动取消) 后通知 show-service 释放座位的事件载体
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderClosedEventDTO implements Serializable {

    private String messageId;
    private String orderNo;
    private Long userId;
    private Long sessionId;
    private List<Long> seatIds;
    private String closeReason;
}
