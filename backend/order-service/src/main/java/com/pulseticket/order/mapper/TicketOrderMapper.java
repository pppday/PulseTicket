package com.pulseticket.order.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.pulseticket.order.entity.TicketOrder;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface TicketOrderMapper extends BaseMapper<TicketOrder> {

    /**
     * 订单状态机 CAS 原子支付：仅当 status = 'UNPAID' 时允许变为 PAID
     */
    @Update("""
            UPDATE ticket_order
            SET status = 'PAID',
                ticket_code = #{ticketCode},
                paid_at = NOW(3),
                version = version + 1
            WHERE order_no = #{orderNo}
              AND user_id = #{userId}
              AND status = 'UNPAID'
            """)
    int casMarkOrderPaid(
            @Param("orderNo") String orderNo,
            @Param("userId") Long userId,
            @Param("ticketCode") String ticketCode
    );

    /**
     * 订单状态机 CAS 原子关单：解决“第14分59秒付款与第15分00秒死信关单并发竞态”
     * 若用户已在前1毫秒完成支付 (status='PAID')，则此 SQL affectedRows = 0，安全跳过关单！
     */
    @Update("""
            UPDATE ticket_order
            SET status = 'CLOSED',
                close_reason = #{closeReason},
                closed_at = NOW(3),
                version = version + 1
            WHERE order_no = #{orderNo}
              AND status = 'UNPAID'
            """)
    int casCloseUnpaidOrder(
            @Param("orderNo") String orderNo,
            @Param("closeReason") String closeReason
    );
}
