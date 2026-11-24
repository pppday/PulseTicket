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
 * 站内信通知实体（映射 `ticket_user.user_message` 表）。
 * 用于存放新人欢迎信、电子票签发通知、阶梯退票办结通知等消息。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("user_message")
public class UserMessage {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long userId;
    private String orderNo;
    private String msgType;
    private String title;
    private String content;
    private Integer isRead;
    private LocalDateTime createdAt;
}
