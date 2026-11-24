package com.pulseticket.user.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.user.entity.UserAccount;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

/**
 * 会员账号表（user_account）Mapper。
 * 提供数据库层原子增减积分与成单数的 SQL（退票冲正使用 GREATEST(0, ...) 保证积分永不扣成负数）。
 */
@Mapper
public interface UserAccountMapper extends BaseMapper<UserAccount> {

    @Update("UPDATE user_account SET points_balance = points_balance + #{delta}, total_orders = total_orders + 1 WHERE id = #{userId}")
    int addPointsAndOrderCount(@Param("userId") Long userId, @Param("delta") int delta);

    @Update("UPDATE user_account SET points_balance = GREATEST(0, points_balance - #{delta}), total_orders = GREATEST(0, total_orders - 1) WHERE id = #{userId}")
    int deductPointsAndOrderCount(@Param("userId") Long userId, @Param("delta") int delta);
}
