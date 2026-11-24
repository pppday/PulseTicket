package com.pulseticket.order;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.cloud.openfeign.EnableFeignClients;

/**
 * 订单微服务（order-service）启动入口
 * 作用：负责票务交易核心链路——下单（Redisson 分布式锁 + Seata AT 分布式事务）、
 * 模拟支付、阶梯退票、RabbitMQ 延迟超时自动关单、以及电子票核验。
 * 通过 @EnableFeignClients 远程调用 show-service 锁定和释放座位库存。
 */
@SpringBootApplication
@EnableDiscoveryClient
@EnableFeignClients(basePackages = "com.pulseticket.feign.client")
@MapperScan("com.pulseticket.order.mapper")
public class OrderServiceApplication {

    /** 订单微服务主启动入口 */
    public static void main(String[] args) {
        SpringApplication.run(OrderServiceApplication.class, args);
    }
}
