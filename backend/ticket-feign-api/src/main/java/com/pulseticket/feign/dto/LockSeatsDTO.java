package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 远程锁座请求参数对象（DTO）
 * 作用：order-service 下单时通过 ShowSeatClient.lockSeats 发给 show-service，
 * 告诉库存服务“哪笔订单、哪个用户、想锁哪一场演出的哪几个座位”。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class LockSeatsDTO implements Serializable {

    /** 订单号（锁座成功后会写进 seat_inventory.lock_order_no，用来绑定座位归属） */
    private String orderNo;
    /** 下单用户 ID */
    private Long userId;
    /** 演出场次 ID */
    private Long sessionId;
    /** 待锁定的座位主键 ID 列表 */
    private List<Long> seatIds;
}
