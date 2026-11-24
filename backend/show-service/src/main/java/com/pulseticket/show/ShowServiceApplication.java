package com.pulseticket.show;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 演出与库存微服务启动类（端口 :8202）。
 * 负责演出目录展示、Redis 缓存三防、48 座选座矩阵、CAS 锁座/释座、权威核价及阿里云 OSS 海报直传。
 */
@SpringBootApplication
@EnableDiscoveryClient
@EnableScheduling
@MapperScan("com.pulseticket.show.mapper")
public class ShowServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(ShowServiceApplication.class, args);
    }
}
