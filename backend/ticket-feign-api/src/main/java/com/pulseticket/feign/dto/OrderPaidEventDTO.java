package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.List;

/**
 * 支付成功后向 RabbitMQ Fanout 广播的事件载体
 * 1) show-service 监听将座位从 LOCKED -> SOLD
 * 2) user-service 监听增加用户积分并发送电子票签发站内信 (靠 UNIQUE(order_no, biz_type) 保证消费幂等)
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderPaidEventDTO implements Serializable {

    private String messageId;
    private String orderNo;
    private Long userId;
    private String username;
    private Long showId;
    private String showTitle;
    private String venueName;
    private Long sessionId;
    private String sessionTitle;
    private List<Long> seatIds;
    private List<String> seatLabels;
    private BigDecimal totalAmount;
    private String ticketCode;
}
