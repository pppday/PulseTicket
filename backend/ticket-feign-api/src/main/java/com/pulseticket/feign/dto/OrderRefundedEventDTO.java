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

    /** 事件消息唯一 ID */
    private String messageId;
    /** 退票订单号 */
    private String orderNo;
    /** 退票用户 ID */
    private Long userId;
    /** 演出名称 */
    private String showTitle;
    /** 场次标题 */
    private String sessionTitle;
    /** 订单原始总金额 */
    private BigDecimal totalAmount;
    /** 阶梯退票手续费金额 */
    private BigDecimal feeAmount;
    /** 阶梯退票手续费比例文案（如 "0%"、"20%"） */
    private String feeRate;
    /** 实际退还给用户的金额 */
    private BigDecimal refundAmount;
    /** 命中的退票档位说明（如 "开演前 48 小时以上免手续费"） */
    private String tierName;
    /** 本次需要从用户账户冲正扣回的观演积分 */
    private Integer pointsToDeduct;
}
