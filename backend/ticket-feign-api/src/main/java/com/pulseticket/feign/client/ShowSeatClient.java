package com.pulseticket.feign.client;

import com.pulseticket.common.result.Result;
import com.pulseticket.feign.dto.LockSeatsDTO;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.SeatLockResultVO;
import com.pulseticket.feign.fallback.ShowSeatClientFallbackFactory;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

/**
 * 演出座位库存远程调用契约 (存放于独立 feign-api 模块，解决服务间循环依赖)
 * 作用：order-service 通过这个 OpenFeign 接口像调本地方法一样，走内网 HTTP 调用 show-service 的内部锁座/释座接口；
 * 当 show-service 宕机、超时或触发 Sentinel 熔断时，自动走 ShowSeatClientFallbackFactory 降级兜底。
 */
@FeignClient(
        name = "show-service",
        contextId = "showSeatClient",
        fallbackFactory = ShowSeatClientFallbackFactory.class
)
public interface ShowSeatClient {

    /**
     * 下单同步锁定座位 (参与 Seata AT 分布式事务)
     * 对应 show-service 的 ShowInternalController#lockSeats，扣减/占用库存并返回演出场次与总价快照。
     */
    @PostMapping("/internal/show/seats/lock")
    Result<SeatLockResultVO> lockSeats(@RequestBody LockSeatsDTO dto);

    /**
     * 释放超时/取消/退票订单绑定的座位
     * 对应 show-service 的 ShowInternalController#releaseSeats，把座位归还为 AVAILABLE 并刷新缓存。
     */
    @PostMapping("/internal/show/seats/release")
    Result<Integer> releaseSeats(@RequestBody OrderClosedEventDTO dto);
}
