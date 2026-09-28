package com.amazon.kindle.kindlet;

import java.awt.Container;

public interface KindletContext {
    Container getRootContainer();
    boolean requestPermission(java.security.Permission permission);
}
