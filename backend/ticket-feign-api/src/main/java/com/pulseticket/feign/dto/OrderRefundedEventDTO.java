package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.math.BigDecimal;

/**
 * 已支付订单阶梯退票成功后向 RabbitMQ Fanout 广播的事件载体
 * - user-service 监听消费，按 UNIQUE(order_no, biz_type) 幂等冲正扣回该笔订单发放的观演积分与累计单数，并下发退票站内信
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderRefundedEventDTO implements Serializable {

    private String messageId;
    private String orderNo;
    private Long userId;
    private String showTitle;
    private String sessionTitle;
    private BigDecimal totalAmount;
    private BigDecimal feeAmount;
    private String feeRate;
    private BigDecimal refundAmount;
    private String tierName;
    private Integer pointsToDeduct;
}
