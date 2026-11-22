package com.pulseticket.user.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.user.entity.UserAccount;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface UserAccountMapper extends BaseMapper<UserAccount> {

    @Update("UPDATE user_account SET points_balance = points_balance + #{delta}, total_orders = total_orders + 1 WHERE id = #{userId}")
    int addPointsAndOrderCount(@Param("userId") Long userId, @Param("delta") int delta);
}
