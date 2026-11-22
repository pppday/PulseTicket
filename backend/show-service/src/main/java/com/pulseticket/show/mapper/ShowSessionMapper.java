package com.pulseticket.show.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.show.entity.ShowSession;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface ShowSessionMapper extends BaseMapper<ShowSession> {

    @Update("""
            UPDATE show_session s
            SET available_seats = (SELECT COUNT(*) FROM seat_inventory WHERE session_id = #{sessionId} AND status = 'AVAILABLE'),
                locked_seats    = (SELECT COUNT(*) FROM seat_inventory WHERE session_id = #{sessionId} AND status = 'LOCKED'),
                sold_seats      = (SELECT COUNT(*) FROM seat_inventory WHERE session_id = #{sessionId} AND status = 'SOLD'),
                version = version + 1
            WHERE s.id = #{sessionId}
            """)
    int syncSessionSeatCounts(@Param("sessionId") Long sessionId);
}
