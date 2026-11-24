package com.pulseticket.show.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.show.entity.ShowInfo;
import org.apache.ibatis.annotations.Mapper;

/** 演出基础信息表（show_info）MyBatis-Plus 通用 CRUD Mapper。 */
@Mapper
public interface ShowInfoMapper extends BaseMapper<ShowInfo> {
}
