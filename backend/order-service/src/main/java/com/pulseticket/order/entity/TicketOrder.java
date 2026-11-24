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

/**
 * 票务订单核心实体类，对应数据库表 `ticket_order`
 * 作用：保存一笔订单从创建锁座（UNPAID）-> 支付出票（PAID）-> 超时关单/用户取消/阶梯退票（CLOSED）的全生命周期快照，
 * 包含乐观锁版本号 version、防重幂等键 idempotencyKey、Seata XID 以及入场电子票核验码 ticketCode。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("ticket_order")
public class TicketOrder {

    @TableId(type = IdType.AUTO)
    private Long id;
    /** 业务唯一订单号（如 TK2026...） */
    private String orderNo;
    /** 外部请求幂等键，配合 (user_id, idempotency_key) 唯一索引防止网络超时重试导致重复下单 */
    private String idempotencyKey;
    /** 下单用户 ID */
    private Long userId;
    /** 下单用户名快照 */
    private String username;
    /** 演出主表 ID */
    private Long showId;
    /** 演出名称快照 */
    private String showTitle;
    /** 演出场馆快照 */
    private String venueName;
    /** 场次 ID */
    private Long sessionId;
    /** 场次标题快照 */
    private String sessionTitle;
    /** 开演时间快照（退票时据此计算距开演小时数以决定手续费档位） */
    private LocalDateTime startTime;
    /** 选座 ID 列表的 JSON 数组字符串 */
    private String seatIdsJson;
    /** 选座人类可读标签的 JSON 数组字符串 */
    private String seatLabelsJson;
    /** 购票张数 */
    private Integer seatCount;
    /** 订单总金额（由 show-service 权威计算回传） */
    private BigDecimal totalAmount;
    /** 订单状态机：UNPAID 待支付 / PAID 已支付 / CLOSED 已关闭（含超时关单、取消、退票） */
    private String status;
    /** 关单或退票原因说明 */
    private String closeReason;
    /** 本单设置的超时未支付自动关单延迟秒数（默认 900 秒即 15 分钟，演示时可调短如 30 秒） */
    private Integer delaySeconds;
    /** 下单时关联的 Seata 全局事务 XID */
    private String seataXid;
    /** 支付成功后生成的唯一电子票核验码（如 PT-xxxx-xxxx） */
    private String ticketCode;
    /** 支付截止时间 */
    private LocalDateTime expireAt;
    /** 实际支付完成时间 */
    private LocalDateTime paidAt;
    /** 实际关单/退票时间 */
    private LocalDateTime closedAt;
    /** 乐观锁版本号，每次状态变更原子 +1 */
    private Integer version;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
