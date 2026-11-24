package com.pulseticket.user.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.user.entity.PointsLog;
import org.apache.ibatis.annotations.Mapper;

/** 会员积分流水表（points_log）Mapper。 */
@Mapper
public interface PointsLogMapper extends BaseMapper<PointsLog> {
}
