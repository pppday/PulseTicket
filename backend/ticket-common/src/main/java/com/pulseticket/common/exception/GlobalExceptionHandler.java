package com.pulseticket.common.exception;

import com.pulseticket.common.result.Result;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.stream.Collectors;

/**
 * 全局统一异常处理器（所有业务微服务的“兜底安全网”）
 * 作用：拦截 Controller 层抛出的各种异常，避免把丑陋的 Java 堆栈直接暴露给前端，
 * 而是统一封装成标准的 Result JSON（包含状态码、可读的中文提示和 traceId）。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    /**
     * 拦截自定义的业务异常（如余票不足、重复下单、无管理员权限等）
     * 根据异常里带的 code 自动映射成对应的 HTTP 状态码返回。
     */
    @ExceptionHandler(BizException.class)
    public ResponseEntity<Result<Void>> handleBizException(BizException ex) {
        log.warn("Business exception [{}]: {}", ex.getCode(), ex.getMessage());
        HttpStatus status = HttpStatus.resolve(ex.getCode());
        if (status == null) {
            status = HttpStatus.BAD_REQUEST;
        }
        return ResponseEntity.status(status).body(Result.fail(ex.getCode(), ex.getMessage()));
    }

    /**
     * 拦截 Spring Validation（@Valid / @NotNull 等）参数校验失败异常
     * 把多个字段的校验失败原因用分号拼成一句话返回给前端。
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Result<Void>> handleValidation(MethodArgumentNotValidException ex) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .map(FieldError::getDefaultMessage)
                .collect(Collectors.joining("; "));
        return ResponseEntity.badRequest().body(Result.fail(400, msg));
    }

    /**
     * 兜底拦截所有未预料到的系统级异常（如空指针、数据库断连等），打印完整错误日志并返回 500
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Result<Void>> handleGeneral(Exception ex) {
        log.error("Unhandled server exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Result.fail(500, ex.getMessage() != null ? ex.getMessage() : "服务器内部异常"));
    }
}
