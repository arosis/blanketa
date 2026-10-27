/*
 * JS which handles "general" zfo events:
 * - show/hide multicases in multiswitches
 * - reacting to field changes, storing values immediately to the data model
 * - storing whole form to the file 
 */
var FIELD_COLORS = {"editable": "yellow",
						  "text": "black",
						  "readonly" : "grey",
						  "bad_number" : "#FF9E3D",
						  "constr_violated" : "red"};

var GlobalVars = {'handlers_active' : 0,
									'adding_rs_locked': false,
									'dateinput_counter': 0,
									'input_counter': 0}; 

/*
 * Prepare the "workplace" when doc is ready
 */
$(document).ready(function() {
	send(JSON.stringify({"type":"document.ready", "data": ""}));		
	// allow user clear the date input
  set_date_inputs_clear_func();
	// prepare pending_request area - area spans over whole body and its cursor is 'wait'
	prepare_pending_request_area();
		
	$("#main_area").css("border-top", "0px");
	
	// set escaped id of open document
	var doc_id = escape(window.location.href.split('zfo=')[1]);
	$("#current_zfo_name").attr("title", doc_id);
	//add onchange action to each input
	add_input_text_onchange();
	add_input_radio_onchange();
	add_input_check_onchange();
	// inputs of type 'file'
	set_add_attachment_clicked();
	set_del_attachment_clicked();
	
	add_select_onchange()
		
	//disable 602 repetition control buttons
	hide_602_repe_controls();
	
	//set event handler for field info request
	set_field_info_request_handler();
	// set inputs as readonly
	// set_readonly_inputs();
	
	//send templates of repeating sections to the application before they are filled
	send_rep_sections_originals();	
		
	// initialize the repeating sections in html
	init_repeating_sections();
	
	//
	// Identification of inputs and repe section is done
	// in init_repeating_sections callback. Readonly inputs,
	// multiswitches and listboxes are also set there
		
});//doc ready 


/*
 * Asks for the repeating sections that are available when the document is opened.
 */
function init_repeating_sections(){
	gtk_handler_start();	   
	send(JSON.stringify({"type": "init_rep_sections", "data":""}))
}

function init_rep_sections_callback(data){
	populate_repeating_sections(null, data);
	// after the repeating sections are initialized, mark readonly inputs
	// and set initial values
	set_readonly_inputs();
	// identify inputs globally
	identify_inputs(null);	
	// date inputs must be identified before the inputs are upgraded to datepickers
	identify_date_inputs(null);
	// set date inputs   
	prepare_date_inputs(null);
	identify_repe_sections();
	//set initial values to inputs
	set_inputs_init_values();	
	
	// get the listboxes and their selected values
	get_selected_listboxes();
	// show/hide multiswitch controls according to the actual data model values
	set_multiswitch_controls();	
	
	gtk_handler_finished();
}

function get_html_source(){
	var html = $('html').html();
	//send(JSON.stringify(html));
	send(JSON.stringify({"type": "show_source", "data":html}));
}

/*
 * Sends html templates of repeating sections. Html's are in a dictionary,
 * key is a name of bind.
 */
function send_rep_sections_originals(){
	var reps = {};
	$('[class^="602-repetition-"]').each(
			function(){
				var name = $(this).attr("name");
				//var tag = "<"+this.tagName+">";
				//var html = $(tag).append($(this).clone()).remove().html();
				var html = $(this).wrap('<div>').parent().html();
				$(this).unwrap('<div>');								
				reps[name] = html;			
			});	
	gtk_handler_start();
	send(JSON.stringify({"type" : "set_html_templates", "data" : reps}));
}

/*
 * Set onchange and onclick event handlers for inputs.
 */
function add_input_radio_onchange(){
	$('input:radio').bind('change.zfo', function(){
					field_changed_handler(this);
					});
}

function add_input_check_onchange(){
	$('input:checkbox').bind('change.zfo', function(){
					field_changed_handler(this);
					});
}
			
function add_input_text_onchange(){
	$('input:text:not(.fileInput)').bind('change.zfo', function(){
					field_changed_handler(this);
					});
}

function add_select_onchange(){
	$('select').bind('change.zfo', function(){
				field_changed_handler(this);
				});
}	

function set_add_attachment_clicked(){
	$('button[name="add_attachment"]').bind('click.zfo', function(){
					attachfile_click_handler(this);
					});
}


function set_del_attachment_clicked(){
	$('button[name="del_attachment"]').bind('click.zfo', function(){
					del_attachfile_click_handler(this);
					});
}


/*
 * This function sets keypress handler for date inputs.
 * Content of input is deleted if user presses delete or backspace key
 */
function set_date_inputs_clear_func(){
	//webkit generates only keydown event for 'Delete' key
	$(".date-pick").keydown(function(event){
		// 8 = backspace, 46 = delete		
		if ((event.keyCode == '8') || (event.keyCode == '46')){			
			if ($(this).val() == ""){return;};			
			$(this).val("");
			field_changed_handler(this);		
		}; 	
	});
}

/*
 * Function sends request for input information.
 * This is one-way command, no reply returns, application
 * only sets field information to its GUI.
 * "this" is set to the input which gained focus.
 */
function field_info_request_handler(){
	var outgoing = {
			"id": this.id,
			"name" : $(this).attr("name"), 
			"text": $(this).attr("title"), 
			"ro" : $(this).attr("readonly")
			};
	send(JSON.stringify({"data": outgoing, "type": "get_input_info"}));	
}

/*
 * Set handlers for the focus event. When input is focused,
 * send query to get input information.
 */
function set_field_info_request_handler(){
	$("input").unbind("focus.getInfo");
	$("select").unbind("focus.getInfo");
	
	$("input[type='text'], select").bind('focus.getInfo', field_info_request_handler);	
	$("input[type='radio']").bind('focus.getInfo', field_info_request_handler);
	$("input[type='check']").bind('focus.getInfo', field_info_request_handler);
	// mark the selected field	
	$("input").bind('focus.getInfo', function(){		
		$("input").removeClass("selectedInput");
		$(this).addClass("selectedInput")	
	});
}

/*
 * Asks application for initial document values.
 * Response is processed in set_inputs_init_values_callback
 */
function set_inputs_init_values(){
	var outgoing = new Object();
	
	var texts = $("input[type='text'], input[type='date']");
	for (var i=0; i< texts.length; i++){
		var text_input = texts[i];
		var id = $(text_input).attr("id");
		var name = $(text_input).attr("name");
		outgoing[id] = {'type': 'text', 'bind':name};
	}
	var radios = $("input[type='radio']");
	for (var i=0; i< radios.length; i++){
		var radio_input = radios[i];		
		var name = $(radio_input).attr("name");
		//outgoing.radio[id] = name;
		outgoing[name] = {'type': 'radio', 'bind':name};
	}
	var checks = $("input[type='checkbox']");
	for (var i=0; i< checks.length; i++){
		var check_input = checks[i]; 
		var id = $(check_input).attr("id");
		var name = $(check_input).attr("name");
		//outgoing.checkbox[id] = name;
		outgoing[id] = {'type': 'check', 'bind':name};
	}
	var outputs = $(".outputelement");
	for (var i=0; i< outputs.length; i++){
		var output = outputs[i]; 
		var id = $(output).attr("id");
		var name = $(output).attr("name");
		outgoing[id] = {'type': 'output', 'bind':name};
	}
		
	gtk_handler_start();	   
	send(JSON.stringify({"data": outgoing, "type": "get_init_values"}));
}

/*
 * Function that process response requested by set_inputs_init_values.
 */
function set_inputs_init_values_callback(data){	
	// set values to inputs
	for (var item in data){
		var input = $("#"+clean_str(item));
		// special case - type "out" - user cannot change this value
		if (input.hasClass('outputelement')){
			// this is output element. Only put the text inside div/span.
			input.html(data[item].value);
			continue;
		}
		if (data[item].type == 'radio'){			
			set_radio_value(data[item].bind, data[item].value);		
		}
		else{					
			set_input_value(input, data[item].value);			
		}	
	}
	// fill in repeatable sections
	calc_repeatable_sections();
	gtk_handler_finished();
}

/*
 * Asks server for the ist of selected listboxes (selects)
 */
function get_selected_listboxes(){
	gtk_handler_start();	   
	send(JSON.stringify({"type":"get_selected_listboxes", "data": ""}));
}

/*
 * Function sets listbox data. If the type of data is 'itemtext',
 * sets according to the option's text, if the type is 'value'
 * sets according to the option's value.
 */
function get_selected_listboxes_callback(data){
// note: repeating section listboxes will be handled in other function	
	for (var lb_name in data){
		//get the listbox		
		var lb = $("select[name='"+clean_str(lb_name)+"']");
		var select_data = data[lb_name];
		if (select_data["type"] == "itemtext"){
			set_select_by_optiontext(lb, select_data["value"]);
		}
		if (select_data["type"] == "value"){
			set_select_by_value(lb, select_data["value"]);		
		}
		
	}
	gtk_handler_finished();
}



/*
 * Asks server for the list of repeatable sections
 */
function calc_repeatable_sections(){	
	gtk_handler_start();	   
	send(JSON.stringify({"type": "get_repeatable", "data":""}))
}

/*
 * Processing response to the repeatable sections query.
 */
function calc_repeatable_sections_callback(data){
	fill_inputs(null, data);
	//identify_inputs();
	//identify_repe_sections();
	gtk_handler_finished();
}




/*
 * Asks for list of multiswitches in document. Then in response processing 
 * hides fields, which are not visible 
 * (their name is other that the name in the data model)
 */
function set_multiswitch_controls(){
	gtk_handler_start();	   
	send(JSON.stringify({"type":"get_multiswitch_list", "data": ""}));
}

/*
 * Shows and hides multicases, callback for set_multiswitch_controls
 */
function set_multiswitch_controls_callback(data){
	var switches = data.data;
	for (var i=0; i< switches.length; i++){
		var switx = switches[i];
		
		var sw_name = switx.bindref;
		var model_value = switx.active_value;
		
		for (var j = 0; j< switx.cases.length; j++){
			var id = 'div-' + sw_name + '-' + switx.cases[j];
			if (model_value != switx.cases[j]){
				//alert("Compared "+ model_value +" and "+ parts[j]+" Hiding "+ id);
				$("div[name='"+clean_str(id)+"']").hide();
			}
			else{
				//alert("Values "+ model_value + " and " + parts[j]+" are the same");
				//alert("SHowing "+ id);
				$("div[name='"+clean_str(id)+"']").show();
			}
		}
	}
	gtk_handler_finished();
}


/*
 * Function called after successfull save of a field value.
 */
function after_store_handler(calculated_data){
	fill_inputs(null, calculated_data);
	//!! after the form is recalculated, refresh repeating sections	
	// after form is recalculated, modify repeatable sections	
	calc_repeatable_sections();
	//ans set readonly inputs and multiswitches	
	set_readonly_inputs();
	set_multiswitch_controls();
	gtk_handler_finished();
}

/*
 *Data stored, asks for form recalculation.
 */
function field_changed_ok_callback(data){
	// after successful save of field, recalculate others
	var field = $("#"+clean_str(data['input_id']));
	$(field).css("color", FIELD_COLORS["text"]);
	// this is done while storing data, do not recalculate	
	// recalculate_form(field, data["repe"]);
	// run post_change handler
	after_store_handler(data["calculated"]);
	
	gtk_handler_finished();	
}

/*
 * Saving of value failed.
 */
function field_changed_error_callback(data){
	//put information about storing into the message area
	var inp = $("#"+clean_str(data['input_id']));
	var val = data['curr_val'];
	// do or do not set the former value?
	//set_input_value(inp, val);
	$(inp).css("background-color", FIELD_COLORS["constr_violated"]);
	gtk_handler_finished();
}

/*
 * Handler for onchange event. In input loses focus, store the data set into that input
 */
function field_changed_handler(field){
	if ($(field).hasClass("numberInput")){
		if (!is_number(field)){
			//alert("Value "+ field.value + " is not a valid number and won't be stored! ");	
			field.focus();
			field.select();			
			return;	
		}	
	}
	// here will come code savig current value of the field. recalculating the readonly field and calculating depending fields				
	//var zfo_id = get_current_zfo_name();
	// get the value of field. In case of checkbox, set value to on/off
	var fieldval = null;
	// checkbox
	if ($(field).attr("type") == "checkbox"){
		var checked = $(field).attr("checked");
		fieldval = "off";
		if (checked) fieldval = "on"
	}
	else{
		// select
		var tagname = field.tagName.toLowerCase();
		if (tagname == "select"){						
			if ($(field).val() == ""){		
				// if value is "", send index of selected item	
				var idx = $(field).attr("selectedIndex");
				fieldval = field.options[idx].text;
				//alert(fieldval);
			}
			else{
				fieldval = $(field).val();
			}		
		}
		// text or radio
		else{
			fieldval = $(field).val();
		}
	}	
	//if we had a number input, replace , with . for xslt
	if ($(field).hasClass("numberInput")){
		fieldval = fieldval.replace(',', '.');	
	}	
	// get information about parent elements
	var repe = get_parent_repe(field);
	//alert(field.name + " -> " +fieldval);	
	var data = {"field" : field.name,
					"value" : fieldval,
					"repe"  : repe,
					"input_id": field.id				
					};
	gtk_handler_start();	   
	send(JSON.stringify({"type": "store_val", "data": data}));
}	


/*
 * Handlers and callbacks for handling inputs of type 'file'.
 */
function attachfile_click_handler(button){
	var input_field = $(button).prev();
	var repe = get_parent_repe(input_field);	
	var data = {"field" : $(input_field).attr("name"),
					"value" : $(input_field).val(),
					"repe"  : repe,
					"input_id": $(input_field).attr("id"),
					"filter": $(input_field).attr("accept")			
					};
	gtk_handler_start();	   
	send(JSON.stringify({"type": "select_file", "data": data}));
}

function del_attachfile_click_handler(button){
	var input_field = $(button).prev().prev();
	var repe = get_parent_repe(input_field);	
	var data = {"field" : $(input_field).attr("name"),
					"value" : $(input_field).val(),
					"repe"  : repe,
					"input_id": $(input_field).attr("id"),
					"filter": $(input_field).attr("accept")			
					};
	gtk_handler_start();	   
	send(JSON.stringify({"type": "delete_attachment", "data": data}));
}

function append_file_callback(response){
	if (response.filename != ""){	
		var inputid = response.input_id;
		var value = response.filename
		$("#"+clean_str(inputid)).val(value);
		$("#"+clean_str(inputid)).attr("title", value);
		// unset flag from inputs, that says that the input should stay empty
		var filename_input_name = response.filename_bind_name;
		var mediatype_input_name = response.mediatype_bind_name;
		var inp = $("#"+clean_str(inputid));
		var closest_rep_el = inp.closest(".602-repetition-block, .602-repetition-row, .602-repetition-table");		
		closest_rep_el.find('[name="'+filename_input_name+'"]').first().removeClass("shouldStayEmpty");	
		closest_rep_el.find('[name="'+mediatype_input_name+'"]').first().removeClass("shouldStayEmpty");	
	}
	fill_inputs(null, response.calculated_data);	
	
	calc_repeatable_sections();	
	set_readonly_inputs();
	set_multiswitch_controls();
	
	gtk_handler_finished();
}

function delete_file_callback(response){		
	var inputid = response.input_id;	
	$("#"+clean_str(inputid)).val("");	
	$("#"+clean_str(inputid)).attr("title", "-Prazdny-");
	
	fill_inputs(null, response.calculated_data);	
	calc_repeatable_sections();	
	set_readonly_inputs();
	set_multiswitch_controls();
	
	// delete filename and mediatype input in local repeating section and 
	// set them a flag they should stay empty 
	var filename_input_name = response.filename_bind_name;
	var mediatype_input_name = response.mediatype_bind_name;
	var inp = $("#"+clean_str(inputid));
	var closest_rep_el = inp.closest(".602-repetition-block, .602-repetition-row, .602-repetition-table");
	closest_rep_el.find('[name="'+filename_input_name+'"]').first().val("");
	closest_rep_el.find('[name="'+filename_input_name+'"]').first().addClass("shouldStayEmpty");
	closest_rep_el.find('[name="'+mediatype_input_name+'"]').first().val("");
	closest_rep_el.find('[name="'+mediatype_input_name+'"]').first().addClass("shouldStayEmpty");
	
	
	gtk_handler_finished();
}


/*
 * Request to application to recalclate data in the form according
 * to the change of specified field.
 */
function recalculate_form(field, repe_data){
	var data = {"field": $(field).attr("name"), "repe" : repe_data};
	gtk_handler_start();	   
	send(JSON.stringify({"type": "calculate", "data": data}));
}

/*
 * Callback for recalculate_forms. Sets calculated data into the inputs.
 */
function recalculate_form_callback(data){	
	fill_inputs(null, data);
	//!! after the form is recalculated, refresh repeating sections	
	// after form is recalculated, modify repeatable sections	
	calc_repeatable_sections();
	//and set readonly inputs and multiswitches	
	set_readonly_inputs();
	set_multiswitch_controls();
	gtk_handler_finished();
}

/*
 * Requests server for readonly inputs present in the document
 */
function set_readonly_inputs(){
	gtk_handler_start();	   
	send(JSON.stringify({"type": "get_readonly_binds", "data": ""}));
}		
			
/*
 * Callback for set_readonly_inputs
 * Makes text inputs readonly. Names of the inputs
 * are in data paramater.
 */
function set_readonly_inputs_callback(data){
	// make all inputs editable
	$("input:text:not(.fileInput)").each(function(){  	
			// if this input is not date, make it editable
			if (!$(this).hasClass("date-pick")) {
				$(this).removeAttr("readonly");
			}
			else {
				$(this).datepicker("enable");
			}
			$(this).css("background-color", FIELD_COLORS["editable"]);		
		});
	
	//make file inputs visible
	$("input:text .fileInput").each(
		function(){
			$(this).next("button").show();
			$(this).next("button").next("button").show();		
		}	
	);
		
	// make selects, checkboxes and radios enabled
	$("select input:radio input:checkbox").each(
		function(){
			$(this).removeAttr("disabled");
		}
	);
	
	// make some inputs readonly
	var names=data.readonly_names;
	for (var i = 0; i < names.length; i++){
		var obj = $("input[name='"+ names[i] +"']");
		// if it not input, it may be select
		if (obj.length) {
			if ($(obj).hasClass("fileInput")) {
				$(obj).next("button").hide();
				$(obj).next("button").next("button").hide();
				continue;
			}
			if (obj.attr("type") == "text") {
	  		obj.css("background-color", FIELD_COLORS["readonly"]);	  		
				obj.attr("readonly", "readonly");
				obj.datepicker("disable");
			}
			if ((obj.attr("type") == "checkbox") || (obj.attr("type") == "radio")){	  			  		
				obj.attr("disabled", "disabled");				
			}
		}
		else{
			var sel = $("select[name='"+names[i]+"']");
			sel.attr("disabled", "disabled");			
		}
	}
	
	gtk_handler_finished();		
}

/*
 * This is handler for click action for each button in zfo.
 * It is set during xsl transformation of zfo.
 */
function perform_click_action(control){	
	gtk_handler_start();	   
	send(JSON.stringify({"type": "perform_click", 
											 "data": {"command":$(control).attr('zfo-app-event')}}));											 
}

/*
 *Callback function for button click handler.
 */
function perform_click_action_callback(data){	
	gtk_handler_finished();
}

/*
 *"Empty" callback function used by handlers in applications that 
 * do not send back any response. 
 */
function empty_callback(){
	gtk_handler_finished();
}