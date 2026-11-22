package com.pulseticket.common.result;

import com.pulseticket.common.context.UserContext;
import lombok.Getter;
import lombok.Setter;

import java.io.Serializable;
import java.util.UUID;

/**
 * 统一微服务 REST / Feign 响应包装体
 */
@Getter
@Setter
public class Result<T> implements Serializable {

    private int code;
    private String message;
    private T data;
    private String traceId;
    private long timestamp;

    public Result() {
        this.timestamp = System.currentTimeMillis();
    }

    public Result(int code, String message, T data, String traceId) {
        this.code = code;
        this.message = message;
        this.data = data;
        this.traceId = traceId != null ? traceId : resolveTraceId();
        this.timestamp = System.currentTimeMillis();
    }

    private static String resolveTraceId() {
        String ctxTrace = UserContext.getTraceId();
        if (ctxTrace != null && !ctxTrace.isBlank()) {
            return ctxTrace;
        }
        return "tr-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    public static <T> Result<T> ok() {
        return new Result<>(200, "OK", null, resolveTraceId());
    }

    public static <T> Result<T> ok(T data) {
        return new Result<>(200, "OK", data, resolveTraceId());
    }

    public static <T> Result<T> ok(String message, T data) {
        return new Result<>(200, message, data, resolveTraceId());
    }

    public static <T> Result<T> fail(int code, String message) {
        return new Result<>(code, message, null, resolveTraceId());
    }

    public static <T> Result<T> fail(String message) {
        return new Result<>(500, message, null, resolveTraceId());
    }

    public boolean isSuccess() {
        return this.code == 200;
    }
}
