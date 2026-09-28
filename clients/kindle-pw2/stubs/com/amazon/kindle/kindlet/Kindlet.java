package com.amazon.kindle.kindlet;

public interface Kindlet {
    void create(KindletContext context);
    void start();
    void stop();
    void destroy();
}
