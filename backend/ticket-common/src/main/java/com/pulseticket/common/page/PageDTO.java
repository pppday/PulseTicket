package com.pulseticket.common.page;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.Collections;
import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class PageDTO<T> implements Serializable {

    private long total;
    private long pageNo;
    private long pageSize;
    private List<T> records;

    public static <T> PageDTO<T> of(long total, long pageNo, long pageSize, List<T> records) {
        return new PageDTO<>(total, pageNo, pageSize, records != null ? records : Collections.emptyList());
    }
}
