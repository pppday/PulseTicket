package com.pulseticket.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;

/**
 * PulseTicket 票务平台 API 网关启动类（所有外部流量的统一大门）
 * 作用：作为 Spring Cloud Gateway（底层基于 WebFlux 响应式框架）的入口，
 * 负责将前端和客户端的 HTTP 请求按路由规则分发到 order-service、show-service、user-service 等微服务，
 * 并通过 Nacos 动态发现下游服务实例，同时串联 AuthGlobalFilter 做统一鉴权。
 */
@SpringBootApplication
@EnableDiscoveryClient
public class TicketGatewayApplication {

    /**
     * 网关微服务主启动入口
     */
    public static void main(String[] args) {
        SpringApplication.run(TicketGatewayApplication.class, args);
    }
}
