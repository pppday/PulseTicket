package com.pulseticket.user.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.user.entity.UserMessage;
import org.apache.ibatis.annotations.Mapper;

/** 会员站内信表（user_message）Mapper。 */
@Mapper
public interface UserMessageMapper extends BaseMapper<UserMessage> {
}
