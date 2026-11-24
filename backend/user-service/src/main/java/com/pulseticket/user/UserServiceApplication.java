package com.pulseticket.user;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;

/**
 * 用户与会员积分微服务启动类（端口 :8201）。
 * 负责登录注册签发 JWT、个人档案维护、站内信，以及消费 RabbitMQ 事件异步发放/退票冲正观演积分。
 */
@SpringBootApplication
@EnableDiscoveryClient
@MapperScan("com.pulseticket.user.mapper")
public class UserServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(UserServiceApplication.class, args);
    }
}
