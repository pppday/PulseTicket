package com.pulseticket.order.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.order.entity.MqEventLog;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface MqEventLogMapper extends BaseMapper<MqEventLog> {
}
