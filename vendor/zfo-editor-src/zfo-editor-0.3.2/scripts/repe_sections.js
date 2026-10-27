/*
 * JS function for handling repeating sections in the zfo form.
 * 
 * When a field value is stored, the client must also send information about its
 * ancestors, which could be repeated. This information is used to identify the correct
 * position in the data model.
 *
 * When the document is rendered, the client must ask for data of repeating sections.
 * This data are used to reconstruct stored repeating sections - the repeating sections
 * are copied and filled with data from data model.
 *
 * Script contains also functions used to clone and delete html elements on the client
 * side and data branches on the server side when user is adding and removing 
 * repeating sections.  
 */

/*****************

Functions used to collect repeating ancestors of an input field.

******************/

function build_rep_data_part(elem){
	var result = "";
	
	var last = $(elem).attr("class").lastIndexOf("-");
	var type = $(elem).attr("class").substr(last+1);
	result += type;
	result += '---';
	result += $(elem).attr("name");
	result += "|||";
	
	return result;
}

/*
 * Returns string which tells in which repetition elements the field is nested.
 */
function get_parent_repe(field){
	var rep_buffer = "";
	if ($(field).hasClass("602-repetition-block") || $(field).hasClass("602-repetition-row") || $(field).hasClass("602-repetition-table")){
		rep_buffer += build_rep_data_part(field);
	}
	var parents = $(field).parents(".602-repetition-block, .602-repetition-row, .602-repetition-table");	
	for (var i=0; i < parents.length; i++){
		var parent = $(parents[i]);
		// get the type of parent- block, row pr table
		rep_buffer += build_rep_data_part(parent);
	}
	// cut trailing |||
	return rep_buffer.substring(0, rep_buffer.length - 3);	
}


/*****************

Functions used to reconstruct repeating sections

******************/

/*
 * Fills direct inputs of a repeating block, 
 * runs itself on children, which are rep. blocks 
 */
function fill_inputs(parent_control, data){	
	for (var prop_name in data){
		var value = data[prop_name];
				
		if (is_array(value)) {
		var num_of_copies = data[prop_name].length;
		
		var num_of_html_els;
		var selector_exp = "*[name^='" + prop_name + "-rep-']";
		if (parent_control == null) {
			num_of_html_els = $(selector_exp).length;
		}
		else {
			num_of_html_els = $(selector_exp, parent_control).length;
		}
		// recursively fill in the inputs			
		for (var i = 0; i < num_of_copies; i++) {
			var block_name = prop_name + "-rep-" + i;
			var html_control = get_html_elem(parent_control, block_name, false);
			// update fields in this container
			fill_inputs($(html_control), value[i]);
		}
	}
	else {
		// "normal" value - just set to the input
		var input = get_html_elem(parent_control, prop_name, true);
		if (input == null) {
			continue;
		}
		// fill input if it does not have the flag that it should stay empty set
		if (!$(input).hasClass("shouldStayEmpty")) {		
			set_input_value(input, value);
			}
		}
	}
}

/*
 * Goes through the data and init repeating sections inside the parent_element.
 */
function _populate(parent_control, data, templates){	
	for (var section_name in data){
			var section = data[section_name];
			var num_of_copies = section.length;
			
			var num_of_html_els;
			var selector_exp = "*[name^='" +section_name + "-rep-']";			
			if (parent_control == null){
				num_of_html_els = $(selector_exp).length; 
			}
			else{
				num_of_html_els = $(selector_exp, parent_control).length;
			}			
			var html = templates[section_name];
			// in this cycle only make copies of actual repeating section
			for (var i= 0; i< num_of_copies; i++){
				if(i>=num_of_html_els){
					var prev_name = section_name + "-rep-" + (i -1).toString();				
					var prev_elem = get_html_elem(parent_control, prev_name, false);				
					
					var new_section = append_repeating_section_after(prev_elem, html);
				}
			}			
			// now run recursively on the prepared sections
			for (var a= 0; a < num_of_copies; a++) {
				var elem_name = section_name + "-rep-" + (a).toString();				
				var elem = get_html_elem(parent_control, elem_name, false);				
				_populate(elem, section[a].sub_rs, templates);								
			}			
	}		
}

/*
 * Populates the initial repeating sections in the document.
 */
function populate_repeating_sections(parent_control, data){
	_populate(parent_control, data.sections, data.templates);	
}

/*
 *Sets value to an input. Input can be text, radio, checkbox or select
 */
function set_input_value(input, value){
	if ($(input)[0].tagName.toLowerCase() == "select"){
		// set value of select is special case		
		// trick - if options have some values, set values 				
		if (input.length == 0)			
			// nothing to set			
			return  
		//	set value by selected option text			
		var opt_val = input.options[0].value;
		if (opt_val == ""){
			set_select_by_optiontext($(input), value);
		}
		else{
			set_select_by_value($(input), value)
		}	
	}			
	else{
		switch ($(input).attr("type")){
		case "checkbox":
			set_checkbox_value(input, value);
			break;
		case "radio":
			set_radio_value($(input).attr("name"), value);
			break;
		default:
			$(input).val(value);
		}				
	}
}

function set_checkbox_value(chb, value){
	if ((value==1) || (value=="on") || (value=="checked")){
		$(chb).attr("checked", true);					
	}
	else{
		$(chb).attr("checked", false)					
	}
}

function set_radio_value(radio, value){
	var id = radio+"-"+value;
	var rad = $("#"+clean_str(id));
	if (rad.length != 0)
		rad.attr("checked", "checked");
}

/*
 * Finds html element, which is either an input field or a repeating block
 */
function get_html_elem(parent, elem_name, should_be_input){
	var candidates = null;
	if (parent == null){
		candidates = $("*[name='"+elem_name+"']");
	}
	else{
		candidates = $("*[name='"+elem_name+"']", $(parent));
	}
	// get the current nested level
	var top_depth = $(parent).parents("*[class^='602-repetition-']").length;	
	var curr_level = top_depth + 1;
	var direct_children = [];
	for (var i=0; i< candidates.length; i++){
		var parents = $(candidates[i]).parents("[class^='602-repetition-']");
		//var parents = $(candidates[i]).parents();
		var tmp = $(candidates[i]);
		// if we are looking for input fields, not nested repeating sections
		if (should_be_input){
			if (tmp.hasClass("602-repetition-row") || tmp.hasClass("602-repetition-block") || tmp.hasClass("602-repetition-table")){
				continue;
			}
		}
		// if the input is in nested repeating section
		if (parents.length > curr_level){
			continue;
		}
		else{
			direct_children.push(candidates[i]);
		}	
	}
	// array direct_children contains inputs that are "direct" children of rep block
	// - they are not repetition blocks nor placed inside nested rep blocks	
	if (direct_children.length == 0){
		//alert("Got zero inputs, cannot store value!");
		return null;
		}
	else{
		return direct_children[0];
	}
}

/*
 * Returns true if object is an array.
 * Trick used - contains the constructor of this object string "array"? if yes, it is an array
 */
function is_array(o){	
	if (o == null){
		return false;
	}
	var matched  = o.constructor.toString().match(/array/i);	
	return (matched != null);
}

/*****************

Functions used to clone and delete repeating sections.

******************/

/*
 * Calculates new names for repetition elements.
 * Repetition elements names end with "-rep-n" where n is number from 0 to maxint.
 * Function is given a name of a repetition element. It returns name
 * of following repetition element (increases number by 1). 
 */
function calculate_new_rep_name(original_name){
	// split name by -rep- -> last place in the array will be the number 
	// of repetitions (eg table-rep-0 => 0)	
	
	//can happen if there is no such repeating section in html (strange)
	if (original_name == null){
		return null;
	}
	var tokens = original_name.split("-rep-");
	var len = tokens.length;
	var rep_index = tokens[len-1];
	var new_index = (parseInt(rep_index) + 1);
	//get last "-" to know which part replace with new index
	var last = original_name.lastIndexOf("-");
	var first_part = original_name.substr(0, last);
	// concat the beginning of the name and the new index
	var new_name = first_part + "-" + new_index;	
	return new_name;
}


function server_add_section(control){
	var repe = get_parent_repe(control);
	//clone the data branch on the server
	//var button_id = $(clicked_button).attr("id");
	var rs_id = $(control).attr("id");
	
	var data = {"name": $(control).attr("name"), 
					"rs_id": rs_id, 
					"repe":repe};
					
	gtk_handler_start();	   
	send(JSON.stringify({"type": "add_section", "data":data}));
}

function append_repeating_section_after(after_elem, html_template){
	var elem_to_attach_after = $(after_elem);
	
	var new_name = calculate_new_rep_name(elem_to_attach_after.attr("name"));
	if (new_name == null){
		return null;	
	}
	
	var new_section = $(html_template);
	new_section.attr("name", new_name);
		
	elem_to_attach_after.after(new_section);
	
	// what is the tag of the repeating element?
	tag = $(elem_to_attach_after).get(0).nodeName.toLowerCase();
	
	if (tag == "tr"){	
		//delete last two &lt;td> elements (constaining +/- control) of first row
		$(elem_to_attach_after).children().last().remove();
		$(elem_to_attach_after).children().last().remove();
	}
	if (tag == "table"){
		// remove first tbody element - first child element		
		$(elem_to_attach_after).children().first().remove();	
	}	
	if (tag == "div"){
		//remove first 3 children - 2x span and 1 br		
		$(elem_to_attach_after).children().slice(0,3).remove();
	}
		
	set_handlers_for_cloned_sections(new_section);
	remove_selected_input_class(new_section);
	
	return new_section;
}

function server_add_section_callback(data){	
	var html = data["repe_html"];
	var attach_after = data["attach_after"];
	
	var elem_to_attach_after = $("#"+clean_str(attach_after));
	
	var new_section = append_repeating_section_after(elem_to_attach_after, html);
	
	if (new_section != null) {
  	identify_date_inputs(new_section);
  	prepare_date_inputs(new_section);
  	set_readonly_inputs();
  	identify_inputs(new_section);
  	identify_repe_sections();
  }
	
	gtk_handler_finished();
}


function server_del_section(control){
	var repe = get_parent_repe(control);
	//delete the data branch on the server
	var data = {"name": $(control).attr("name"), "repe":repe};
	gtk_handler_start();	   
	send(JSON.stringify({"type": "del_section", "data":data}));
}

function server_del_section_callback(data){	
	// do the same as if the data changed
	fill_inputs(null, data);
	calc_repeatable_sections();
	//and set readonly inputs and multiswitches	
	set_readonly_inputs();
	set_multiswitch_controls();
	gtk_handler_finished();
}

/*
 * For newly added repeating sections adds handlers for field change and click
 */
function set_handlers_for_cloned_sections(cloned_section){
	// unbind handlers - if they are not unbound, they are called more than once
	cloned_section.find("input:radio, input:text:not(.fileInput), input:checkbox, select").unbind('change.zfo');
	
	// ad handlers for change event on child inputs of new object
	cloned_section.find("input:radio").bind('change.zfo',function(){
					field_changed_handler(this);
							});
	cloned_section.find("input:checkbox").bind('change.zfo',function(){
					chb_changed_handler(this);
							});
	cloned_section.find("input:text:not(.fileInput)").bind('change.zfo', function(){
					field_changed_handler(this);
							});
	cloned_section.find('select').bind('change.zfo', function(){
					field_changed_handler(this);
							});
	
	cloned_section.find('button[name="add_attachment"],button[name="del_attachment"]').unbind('click.zfo');						
	
	cloned_section.find('button[name="add_attachment"]').bind('click.zfo', function(){
					attachfile_click_handler(this);
							});
	cloned_section.find('button[name="del_attachment"]').bind('click.zfo', function(){
					del_attachfile_click_handler(this);
							});
													
	set_field_info_request_handler();
	set_date_inputs_clear_func();	
}

/*
 * Clones row. New row is appended after original.
 * Add/remove controls (+ and -) are removed form the original row.
 */		
function clone_row(row){	
	server_add_section(row);	
}


/*
 * Deletes row. Row is not deleted, if it is last in the table.
 * Add/remove controls (+ and -) are moved to the previous row.
 */
function del_row(row){	
	var buttons_template = '<td ><button onclick="clone_row(this.parentNode.parentNode)" type="button">+</button></td><td ><button onclick="del_row(this.parentNode.parentNode)" type="button">-</button></td>'; 
	var num_of_rows = $(row).siblings("tr.602-repetition-row").length;
	// only one row in the table or tbody
	if (num_of_rows  == 0) return;				
	// append +/- controls to previous row
	$(row).prev().append(buttons_template);
	
	// call this before removing row from html - parents needed
	server_del_section(row);
	$(row).remove();		
}

/*
 * Same as row manipulations, however with divs.
 */
function clone_div(original){	
	server_add_section(original);			
}

/*
 * Same as row manipulations, however with divs.
 */
function del_div(victim){		
	var buttons_template = '<button onclick="clone_div(this.parentNode)" type="button">+</button> <button onclick="del_div(this.parentNode)" type="button">-</button> <br/>';
	var num_of_divs = $(victim).siblings("div.602-repetition-block").length;
	if (num_of_divs == 0) return;	
	$(victim).prev().prepend(buttons_template);		
	server_del_section(victim);
	$(victim).remove();
}

/*
 * Same as row/div manipulations, however with whole tables.
 */
function clone_table(original){	
	server_add_section(original);
}

/*
 * Same as row/div manipulations, however with whole tables.
 */
function del_table(victim){
	var buttons_template = '<tbody><tr> <td><button onclick="clone_table(this.parentNode.parentNode.parentNode.parentNode)" type="button">+</button><button onclick="del_table(this.parentNode.parentNode.parentNode.parentNode)" type="button">-</button></td></tr></tbody>';
	// repetition tables are placed in one container - just count them, their count must be higher than minOccur
	var num_of_tables = $(victim).siblings("table.602-repetition-table").length;
	if (num_of_tables == 0) return;	
	$(victim).prev().prepend(buttons_template);
	server_del_section(victim);
	$(victim).remove();
}

function remove_selected_input_class(obj){
	obj.find("input.selectedInput:last").removeClass("selectedInput");
}
