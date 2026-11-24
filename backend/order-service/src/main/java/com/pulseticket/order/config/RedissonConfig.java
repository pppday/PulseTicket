package com.pulseticket.order.config;

import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 订单服务的 Redisson 分布式锁客户端配置类
 * 作用：向 Spring 容器注入 RedissonClient，供 OrderFacade 在高并发抢票下单时使用
 * MultiLock（联锁）和单座位可重入分布式锁，配合 Watchdog 自动续期防止高并发超卖。
 */
@Configuration
public class RedissonConfig {

    @Value("${spring.data.redis.host:127.0.0.1}")
    private String redisHost;

    @Value("${spring.data.redis.port:6379}")
    private int redisPort;

    /** 创建单机模式 RedissonClient（设置较小连接池以适配低内存部署环境，服务关闭时优雅释放） */
    @Bean(destroyMethod = "shutdown")
    public RedissonClient redissonClient() {
        Config config = new Config();
        config.useSingleServer()
                .setAddress("redis://" + redisHost + ":" + redisPort)
                .setConnectionMinimumIdleSize(2)
                .setConnectionPoolSize(24);
        return Redisson.create(config);
    }
}
