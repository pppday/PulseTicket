package com.pulseticket.order.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.order.entity.SeataTxAudit;
import org.apache.ibatis.annotations.Mapper;

/**
 * `seata_tx_audit` 表的 MyBatis-Plus 数据访问接口，用于读写 Seata 分布式事务提交/回滚审计记录
 */
@Mapper
public interface SeataTxAuditMapper extends BaseMapper<SeataTxAudit> {
}
