package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.List;

/**
 * 锁座成功后的回传快照视图对象（VO）
 * 作用：show-service 锁座成功后，把权威的演出标题、场馆、开演时间、座位标签和实时算出的总价打包还给 order-service，
 * order-service 直接落库生成订单，完全不信任前端传来的金额（防前端篡改价格！）。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class SeatLockResultVO implements Serializable {

    /** 绑定的订单号 */
    private String orderNo;
    /** 演出主表 ID */
    private Long showId;
    /** 演出名称快照 */
    private String showTitle;
    /** 演出场馆名称快照 */
    private String venueName;
    /** 场次 ID */
    private Long sessionId;
    /** 场次标题快照 */
    private String sessionTitle;
    /** 演出开演时间字符串（后续算阶梯退票费率时也要用它） */
    private String startTime;
    /** 实际锁定成功的座位 ID 列表 */
    private List<Long> seatIds;
    /** 座位可视化标签列表（如 "VIP 1排1座"） */
    private List<String> seatLabels;
    /** 由库存服务按数据库实时票价汇总出的权威总金额 */
    private BigDecimal totalAmount;
}
