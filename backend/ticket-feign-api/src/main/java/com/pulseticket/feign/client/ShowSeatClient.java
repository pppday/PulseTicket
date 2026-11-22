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
 */
@FeignClient(
        name = "show-service",
        contextId = "showSeatClient",
        fallbackFactory = ShowSeatClientFallbackFactory.class
)
public interface ShowSeatClient {

    /**
     * 下单同步锁定座位 (参与 Seata AT 分布式事务)
     */
    @PostMapping("/internal/show/seats/lock")
    Result<SeatLockResultVO> lockSeats(@RequestBody LockSeatsDTO dto);

    /**
     * 释放超时/取消订单绑定的座位
     */
    @PostMapping("/internal/show/seats/release")
    Result<Integer> releaseSeats(@RequestBody OrderClosedEventDTO dto);
}
