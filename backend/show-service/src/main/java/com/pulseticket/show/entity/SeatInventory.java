package com.pulseticket.show.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 座位库存实体（映射 `ticket_show.seat_inventory` 表）。
 * 每场演出默认生成 48 座矩阵（6排×8座），核心字段 `status` 流转路径为 AVAILABLE -> LOCKED -> SOLD，
 * 配合 `version` 乐观锁与条件 SQL 实现高并发下零超卖。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("seat_inventory")
public class SeatInventory {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long sessionId;
    private Long showId;
    private Integer rowNo;
    private Integer colNo;
    private String seatLabel;
    private String zoneCode;
    private String zoneName;
    private BigDecimal price;
    private String status;
    private String lockedOrderNo;
    private Long lockedUserId;
    private LocalDateTime lockedAt;
    private LocalDateTime soldAt;
    private Integer version;
}
