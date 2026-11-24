package com.pulseticket.user.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 会员积分变动流水实体（映射 `ticket_user.points_log` 表）。
 * 表上建有联合唯一索引 `(order_no, biz_type)`，既是积分账本，也是 RabbitMQ 消费防重幂等锁。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("points_log")
public class PointsLog {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long userId;
    private String orderNo;
    private String bizType;
    private Integer pointsDelta;
    private Integer balanceAfter;
    private String remark;
    private LocalDateTime createdAt;
}
