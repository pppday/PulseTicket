package com.pulseticket.show.config;

import com.pulseticket.common.context.UserContext;
import io.seata.core.context.RootContext;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Seata 全局事务 XID 跨微服务绑定过滤器
 * 接收上游 OpenFeign RequestInterceptor 透传的 TX_XID 请求头并绑定至当前线程 RootContext，
 * 在请求结束的 finally 中执行 RootContext.unbind() 防止线程复用污染。
 */
@Component
public class SeataXidWebFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {
        String currentXid = RootContext.getXID();
        String headerXid = request.getHeader(UserContext.HEADER_SEATA_XID);
        boolean boundByFilter = false;

        if (currentXid == null && headerXid != null && !headerXid.isBlank()) {
            RootContext.bind(headerXid.trim());
            boundByFilter = true;
        }
        try {
            filterChain.doFilter(request, response);
        } finally {
            if (boundByFilter) {
                RootContext.unbind();
            }
        }
    }
}
