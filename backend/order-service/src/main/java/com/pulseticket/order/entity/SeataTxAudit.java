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
 * Seata 分布式事务审计记录实体类，对应数据库表 `seata_tx_audit`
 * 作用：专门记录每一次跨服务下单的 Seata 全局事务 XID、提交/回滚状态、是否触发了故障注入演练和总耗时，
 * 通过独立事务（REQUIRES_NEW）落库，哪怕下单主事务回滚，审计记录也能完整保留用于面试演示和监控复盘。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("seata_tx_audit")
public class SeataTxAudit {

    @TableId(type = IdType.AUTO)
    private Long id;
    /** Seata 全局事务唯一标识 XID */
    private String xid;
    /** 对应的订单号 */
    private String orderNo;
    /** 下单用户 ID */
    private Long userId;
    /** 演出场次 ID */
    private Long sessionId;
    /** 涉及的座位 ID 列表字符串 */
    private String seatIds;
    /** 事务最终状态（COMMITTED 提交成功 / ROLLED_BACK 触发回滚） */
    private String txStatus;
    /** 是否人工开启了 Seata 故障注入演练（1=是，0=否） */
    private Integer faultInjected;
    /** 事务提交说明或回滚 Undo 补偿摘要 */
    private String undoSummary;
    /** 整个分布式事务执行耗时（毫秒） */
    private Long latencyMs;
    private LocalDateTime createdAt;
}
