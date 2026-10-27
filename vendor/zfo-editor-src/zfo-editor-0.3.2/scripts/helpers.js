/*
 * List of month names for calendar widget
 */
var MonthNames = ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
							"Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"];

/*
 * List of day names for calendar widget. Must start on Sunday.
 */
var DayNamesShort = ["Ne", "Po", "Út", "St", "Čt", "Pá", "So"];


/*
 * Cleans identifier for jquery - jquery does not like ., 
 * we have to escape them with \\.
 * In javascript, replace all method is done through regexp, that's why
 * we have to escape . in pattern /\./g, not just /./g
 */
function clean_str(identifier){	
	return identifier.replace(/\./g, '\\.');
}

function identify_date_inputs(context){
	if (context == null){
		var date_inputs = $("input.date-pick");	
	}
	else{
		var date_inputs = $("input.date-pick", context);
	}
		
	for (var i =0; i<date_inputs.length; i++){
		$(date_inputs[i]).attr("id", "dateinput-"+GlobalVars.dateinput_counter);
		GlobalVars.dateinput_counter++;	
	}
}

/*
 * Go through all inputs and set them and id parameter. Name parameter
 * is occupied 
 */
function identify_inputs(context){
	if (context == null) {
  	var inputs = $("input[type!='radio']:not('.date-pick')");
		var selects = $("select");
		var buttons = $("button");
  }
  else {
  	var inputs = $("input[type!='radio']:not('.date-pick')", context);
		var selects = $("select", context);
		var buttons = $("button", context);
	}
	//count inputs
	for (var i =0; i<inputs.length; i++){
		$(inputs[i]).attr("id", "input-" + GlobalVars.input_counter);
		GlobalVars.input_counter++;	
	}
	//count selects	
	for (var i =0; i<selects.length; i++){
		$(selects[i]).attr("id", "select-" + GlobalVars.input_counter);
		GlobalVars.input_counter++;	
	}
	// count buttons	
	for (var i =0; i<buttons.length; i++){
		$(buttons[i]).attr("id", "button-" + GlobalVars.input_counter);
		GlobalVars.input_counter++;	
	}
}

/*
 * Sets each repeating section an id.
 */
function identify_repe_sections(){
	var repes = $('[class^="602-repetition"]');
	var cntr = 0;
	for (var i =0; i < repes.length; i++){
		var repe = repes[i];
		$(repe).attr("id", "repe-"+cntr);
		cntr+=1;
	}		
}

/*
 * Tries to hide controls of repeating sections
 * specified in form. ZFO Editor uses its own buttons
 * to control repeating sections.
 */
function hide_602_repe_controls(){
	//var buttons = $('button[onclick^="SendAppEvent(addsect"]');
	var buttons = $('button');
	for (var i =0; i< buttons.length; i++){
		var v = $(buttons[i]).val();
		if (v.indexOf('SendAppEvent(addsect') > -1){
			$(buttons[i]).hide();		
		} 	
		if (v.indexOf('SendAppEvent(delsect') > -1){
			$(buttons[i]).hide();		
		} 
	}
}

/*
 * Checks if field value is a number.
 */
function is_number(field){
   // regexp for number
   var re = new RegExp("^[-]?\\d*([\\.,]\\d+|d*)$");
   if (field.value.match(re)) {
     // Content matches 
	 	return true;
   }
   else{
     return false;	
   }
}

/*
 * Handler checks if entered value in a number input is a "correct" number.
 */
function number_input_keypress_handler(field){		
	if (!is_number(field)){		 
		$(field).css("background-color", FIELD_COLORS["bad_number"]);
	}
	else{		
		if ($(field).attr("readonly")){
			$(field).css("background-color", FIELD_COLORS["readonly"]);
		}
		else{
			$(field).css("background-color", FIELD_COLORS["editable"]);
		}		
	}
}

/*
 * Helper functions for setting select value according
 * to value or optiontext.
 */
function set_select_by_value(select, value){
	if (value == ""){
		$(select).children("option").first().attr("selected", "selected");	
	}	
	else{
		$(select).val(value);
	}
}


function set_select_by_optiontext(select, option_text){
	if (select.length == 0) return;	
	//set listbox according to the itemtext
	var ops = select[0].options;
	for (var i = 0; i< ops.length; i++){
		var op = ops[i];
		var is_equal = (op.text == option_text);
		if (is_equal){
		 $(op).attr("selected", "selected");				 
		}
	}
}

/*
 * Helper functions used to show/hide pending request area - 
 * - if some request is pending, html widget is "gray and waiting"
 */
function gtk_handler_finished(){		
	GlobalVars["handlers_active"]--;	
	if (GlobalVars["handlers_active"] == 0){
		//$('body').css({cursor : 'default'});
		$("#pending_request").hide();
	}	
	
}

/*
 * Says html widget to wait for the request result.
 */
function gtk_handler_start(){
	if (GlobalVars["handlers_active"] == 0){		
		$("#pending_request").show();
	}
	GlobalVars["handlers_active"]++;		 	
}

/*
 * Function append to the text inputs with special class
 * a calendar widget
 */
function prepare_date_inputs(context){
	var settings = {
  		showOn: "button",
  		buttonText: "Vybrat",
  		dateFormat: 'yy-mm-dd',
  		firstDay: 1,
  		monthNames: MonthNames,
  		dayNamesMin: DayNamesShort,
  		changeMonth: true,
  		changeYear: true,
  		monthNamesShort: MonthNames,
  		showAnim: 'slideDown',
  		yearRange: '1930:c+20'
  		};
	if (context == null) {		
  	$(".date-pick").datepicker(settings);
  }
	else{		
		$(".date-pick", context).datepicker(settings);
	}
}

/*
 * Prepares area signaling that request is processed by apllication.
 */
function prepare_pending_request_area(){
	var body_w = $("body").width()*10;
	var body_h = $("body").height()*10;
	$("#pending_request").css({display: "block", 
						top: "0px", 
						left: "0px", 
						"background-color": "white",
						position: "absolute",  
						opacity: "0.5", 
						height: body_h, 
						width: body_w,							
						cursor : 'wait',
						});
	$("#pending_request").hide();
}