package org.json.simple.parser;

public class ParseException extends Exception {
    private Object unexpectedObject;

    public ParseException(int position, Object errorType, Object unexpected) {
        unexpectedObject = unexpected;
    }

    public Object getUnexpectedObject() { return unexpectedObject; }
    public void setUnexpectedObject(Object value) { unexpectedObject = value; }
}
