package com.pulseticket.common.page;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.Collections;
import java.util.List;

/**
 * 通用分页返回数据传输对象（DTO）
 * 作用：统一各微服务列表接口的分页出参结构（总条数、当前页码、每页大小、当前页数据列表），
 * 方便前端分页组件直接对接，并兜底保证 records 永不为 null。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class PageDTO<T> implements Serializable {

    /** 符合条件的总记录数 */
    private long total;
    /** 当前页码（从 1 开始） */
    private long pageNo;
    /** 每页展示条数 */
    private long pageSize;
    /** 当前页的实际数据集合 */
    private List<T> records;

    /** 静态工厂方法：如果传入的列表为 null，自动兜底为空集合，防止前端空指针 */
    public static <T> PageDTO<T> of(long total, long pageNo, long pageSize, List<T> records) {
        return new PageDTO<>(total, pageNo, pageSize, records != null ? records : Collections.emptyList());
    }
}
