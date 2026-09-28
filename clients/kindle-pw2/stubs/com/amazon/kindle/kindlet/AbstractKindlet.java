package com.amazon.kindle.kindlet;

public abstract class AbstractKindlet implements Kindlet {
    protected KindletContext context;

    public void create(KindletContext value) { context = value; }
    public void start() { }
    public void stop() { }
    public void destroy() { }
}
