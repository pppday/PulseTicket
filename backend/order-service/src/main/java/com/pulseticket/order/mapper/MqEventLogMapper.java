package com.pulseticket.order.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.order.entity.MqEventLog;
import org.apache.ibatis.annotations.Mapper;

/**
 * `mq_event_log` 表的 MyBatis-Plus 数据访问接口，用于读写 RabbitMQ 消息事件审计日志
 */
@Mapper
public interface MqEventLogMapper extends BaseMapper<MqEventLog> {
}
