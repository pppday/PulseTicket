package com.pulseticket.order.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("ticket_order")
public class TicketOrder {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String orderNo;
    private String idempotencyKey;
    private Long userId;
    private String username;
    private Long showId;
    private String showTitle;
    private String venueName;
    private Long sessionId;
    private String sessionTitle;
    private LocalDateTime startTime;
    private String seatIdsJson;
    private String seatLabelsJson;
    private Integer seatCount;
    private BigDecimal totalAmount;
    private String status;
    private String closeReason;
    private Integer delaySeconds;
    private String seataXid;
    private String ticketCode;
    private LocalDateTime expireAt;
    private LocalDateTime paidAt;
    private LocalDateTime closedAt;
    private Integer version;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
