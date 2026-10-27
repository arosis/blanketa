/*
 * Webkit widget communicates with application 
 * through the change of the document title.
 */
function send(msg) {	
   document.title = "null";
   document.title = msg;    
}

