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
 * 作用：用 MQ 异步解耦支付主链路，让用户点击支付后不用干等发积分、发站内信和更新座位状态。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderPaidEventDTO implements Serializable {

    /** 事件消息唯一 ID */
    private String messageId;
    /** 支付成功的订单号 */
    private String orderNo;
    /** 购票用户 ID */
    private Long userId;
    /** 购票用户名 */
    private String username;
    /** 演出主表 ID */
    private Long showId;
    /** 演出名称（冗余快照，方便站内信直接展示） */
    private String showTitle;
    /** 演出场馆名称 */
    private String venueName;
    /** 场次 ID */
    private Long sessionId;
    /** 场次标题 */
    private String sessionTitle;
    /** 已购座位 ID 列表 */
    private List<Long> seatIds;
    /** 人类可读的座位号标签（如 "A区 1排2座"） */
    private List<String> seatLabels;
    /** 订单实付总金额（user-service 按金额计算发放观演积分） */
    private BigDecimal totalAmount;
    /** 唯一电子票核验码（用于入场扫码验票） */
    private String ticketCode;
}
