package com.pulseticket.feign.fallback;

import com.pulseticket.common.result.Result;
import com.pulseticket.feign.client.ShowSeatClient;
import com.pulseticket.feign.dto.LockSeatsDTO;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.SeatLockResultVO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.openfeign.FallbackFactory;

/**
 * Sentinel + OpenFeign 降级兜底工厂
 * 面试核心考点：
 * 1. 为什么用 FallbackFactory 而不用普通 fallback？因为 FallbackFactory 能捕获具体 Throwable 异常原因打日志；
 * 2. 【重要陷阱】：当 Feign 触发降级返回 Result.fail(503, ...) 时，并没有往外抛 Java 异常，
 *    因此上游 @GlobalTransactional 必须显式判断 if (!result.isSuccess()) throw new BizException(...)，
 *    否则 Seata 会误以为分支调用成功而不触发回滚！
 */
public class ShowSeatClientFallbackFactory implements FallbackFactory<ShowSeatClient> {

    private static final Logger log = LoggerFactory.getLogger(ShowSeatClientFallbackFactory.class);

    @Override
    public ShowSeatClient create(Throwable cause) {
        log.error("ShowSeatClient remote call degraded by Sentinel/Feign: {}", cause.getMessage(), cause);
        return new ShowSeatClient() {
            @Override
            public Result<SeatLockResultVO> lockSeats(LockSeatsDTO dto) {
                return Result.fail(503, "演出座位库存服务触发熔断降级保护: " + cause.getMessage());
            }

            @Override
            public Result<Integer> releaseSeats(OrderClosedEventDTO dto) {
                return Result.fail(503, "座位释放服务暂时不可用，将由 MQ 死信重试补偿: " + cause.getMessage());
            }
        };
    }
}
