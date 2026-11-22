package com.pulseticket.order.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("mq_event_log")
public class MqEventLog {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String messageId;
    private String exchangeName;
    private String routingKey;
    private String orderNo;
    private String eventType;
    private String payloadSummary;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
