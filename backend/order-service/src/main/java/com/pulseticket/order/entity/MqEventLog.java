package com.pulseticket.order.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * RabbitMQ 消息事件审计日志实体类，对应数据库表 `mq_event_log`
 * 作用：把每一条延迟关单、支付广播、退票广播消息的发送/消费轨迹落库保存，
 * 既方便运维排障，也给前端“架构监控大屏”展示实时异步消息流转状态。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("mq_event_log")
public class MqEventLog {

    @TableId(type = IdType.AUTO)
    private Long id;
    /** 消息唯一 ID */
    private String messageId;
    /** 投递的目标交换机名称 */
    private String exchangeName;
    /** 路由键（Routing Key） */
    private String routingKey;
    /** 关联的票务订单号 */
    private String orderNo;
    /** 事件类型（如 ORDER_DELAY_CLOSE / ORDER_PAID_FANOUT / ORDER_REFUNDED_FANOUT） */
    private String eventType;
    /** 给人看的事件详情摘要 */
    private String payloadSummary;
    /** 流转状态（如 SENT / CONSUMED / SKIPPED_ALREADY_PAID） */
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
