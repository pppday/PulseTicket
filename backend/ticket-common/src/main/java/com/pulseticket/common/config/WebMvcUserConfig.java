package com.pulseticket.common.config;

import com.pulseticket.common.exception.GlobalExceptionHandler;
import com.pulseticket.common.interceptor.UserInfoInterceptor;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.web.servlet.DispatcherServlet;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Spring Boot 3 自定义自动装配类 (通过 META-INF/spring/...AutoConfiguration.imports 注册)
 * 仅在 Servlet Web 微服务中生效 (自动排除 WebFlux 的 Gateway 网关)
 * 大白话：只要业务微服务（order / show / user）引入了 ticket-common 依赖，
 * 就会自动获得“登录态提取拦截器”和“全局异常兜底处理器”，不用每个服务重复配一遍！
 */
@AutoConfiguration
@ConditionalOnClass(DispatcherServlet.class)
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
public class WebMvcUserConfig implements WebMvcConfigurer {

    /**
     * 把 UserInfoInterceptor 挂到所有 HTTP 路径上，确保每个请求进来都能把网关传来的用户信息放进 ThreadLocal
     */
    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new UserInfoInterceptor())
                .addPathPatterns("/**");
    }

    /**
     * 自动注册全局异常拦截器，把业务异常和系统报错统一包装成前端认识的 Result JSON 格式
     */
    @Bean
    public GlobalExceptionHandler globalExceptionHandler() {
        return new GlobalExceptionHandler();
    }
}
