package com.pulseticket.show.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.show.entity.SeatInventory;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

import java.util.List;

/**
 * 座位库存数据访问层（MyBatis-Plus Mapper）。
 * 包含防超卖核心 CAS 锁座 SQL（WHERE status='AVAILABLE'）、取消/退票幂等释座 SQL 及支付后转 SOLD SQL。
 */
@Mapper
public interface SeatInventoryMapper extends BaseMapper<SeatInventory> {

    /**
     * 第三道并发防线：数据库条件乐观更新 (WHERE status = 'AVAILABLE')
     * 同时参与 Seata AT 分支事务自动记录 before/after image 到 undo_log
     */
    @Update("""
            <script>
            UPDATE seat_inventory
            SET status = 'LOCKED',
                locked_order_no = #{orderNo},
                locked_user_id = #{userId},
                locked_at = NOW(3),
                version = version + 1
            WHERE session_id = #{sessionId}
              AND status = 'AVAILABLE'
              AND id IN
              <foreach collection="seatIds" item="sid" open="(" separator="," close=")">
                #{sid}
              </foreach>
            </script>
            """)
    int lockAvailableSeats(
            @Param("sessionId") Long sessionId,
            @Param("orderNo") String orderNo,
            @Param("userId") Long userId,
            @Param("seatIds") List<Long> seatIds
    );

    /**
     * 超时未支付关单或取消时：幂等释放 LOCKED -> AVAILABLE
     */
    @Update("""
            UPDATE seat_inventory
            SET status = 'AVAILABLE',
                locked_order_no = NULL,
                locked_user_id = NULL,
                locked_at = NULL,
                version = version + 1
            WHERE session_id = #{sessionId}
              AND locked_order_no = #{orderNo}
              AND status = 'LOCKED'
            """)
    int releaseLockedSeatsByOrder(@Param("sessionId") Long sessionId, @Param("orderNo") String orderNo);

    /**
     * 已支付订单自助退票时：幂等释放 SOLD/LOCKED -> AVAILABLE
     */
    @Update("""
            UPDATE seat_inventory
            SET status = 'AVAILABLE',
                locked_order_no = NULL,
                locked_user_id = NULL,
                locked_at = NULL,
                sold_at = NULL,
                version = version + 1
            WHERE session_id = #{sessionId}
              AND locked_order_no = #{orderNo}
              AND status IN ('LOCKED', 'SOLD')
            """)
    int releaseSoldSeatsByOrder(@Param("sessionId") Long sessionId, @Param("orderNo") String orderNo);

    /**
     * 订单支付成功后：幂等将 LOCKED -> SOLD
     */
    @Update("""
            UPDATE seat_inventory
            SET status = 'SOLD',
                sold_at = NOW(3),
                version = version + 1
            WHERE session_id = #{sessionId}
              AND locked_order_no = #{orderNo}
              AND status = 'LOCKED'
            """)
    int confirmLockedSeatsSold(@Param("sessionId") Long sessionId, @Param("orderNo") String orderNo);
}
