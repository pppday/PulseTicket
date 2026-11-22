package com.pulseticket.show.controller;

import com.pulseticket.common.result.Result;
import com.pulseticket.feign.dto.LockSeatsDTO;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.SeatLockResultVO;
import com.pulseticket.show.service.ShowCacheAndSeatService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 仅供微服务内网 OpenFeign 调用的座位锁控接口 (网关已拦截外部直接访问 /internal/**)
 */
@RestController
@RequestMapping("/internal/show/seats")
@RequiredArgsConstructor
public class ShowInternalController {

    private final ShowCacheAndSeatService showService;

    @PostMapping("/lock")
    public Result<SeatLockResultVO> lockSeats(@RequestBody LockSeatsDTO dto) {
        return Result.ok(showService.lockSeatsInAtTransaction(dto));
    }

    @PostMapping("/release")
    public Result<Integer> releaseSeats(@RequestBody OrderClosedEventDTO dto) {
        return Result.ok(showService.releaseSeatsIdempotent(dto));
    }
}
