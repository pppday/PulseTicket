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
@TableName("seata_tx_audit")
public class SeataTxAudit {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String xid;
    private String orderNo;
    private Long userId;
    private Long sessionId;
    private String seatIds;
    private String txStatus;
    private Integer faultInjected;
    private String undoSummary;
    private Long latencyMs;
    private LocalDateTime createdAt;
}
