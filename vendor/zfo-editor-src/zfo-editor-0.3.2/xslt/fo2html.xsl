<?xml version='1.0' encoding='ISO-8859-1'?>

<!-- =============================================================== -->
<!--                                                                 -->
<!-- Convert XSL FO (as in REC 2001-10-15) to HTML                   -->
<!--                                                                 -->
<!-- RenderX 2000-2001                                             -->
<!-- Permission to copy and modify is granted, provided this notice  -->
<!-- is included in all copies and/or derived work.                  -->
<!--                                                                 -->
<!-- Author: Nikolai Grigoriev, grig@renderx.com                     -->
<!-- 2005-01-04 Johan Knngrd:                                      -->
<!--    Added Xalan namespace, indent=yes, indent amount and changed -->
<!--    encoding to ISO-8859-1                                       --> 
<!--                                                                 -->
<!-- =============================================================== -->


<!DOCTYPE xsl:stylesheet [
  <!ENTITY anchor "<xsl:apply-templates select='@id' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
  <!ENTITY add-style "<xsl:call-template name='add-style-attribute' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
  <!ENTITY add-onclick "<xsl:call-template name='add-onclick-attribute' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
]>

<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:fo="http://www.w3.org/1999/XSL/Format"
				xmlns:fm="http://software602.cz/forms"				
				xmlns:dyn="http://exslt.org/dynamic"
				xmlns:fn="http://www.w3.org/2005/xpath-functions"
				xmlns:exsl="http://exslt.org/common"
				xmlns:dsig="http://www.w3.org/2000/09/xmldsig#"				

       exclude-result-prefixes="fo"
			 xmlns:xalan="http://xml.apache.org/xslt" >

<xsl:output method="html"
            version="4.0"
            encoding="UTF-8"
            doctype-public="-//W3C//DTD HTML 4.0 Transitional//EN"
            indent="yes"
	    xalan:indent-amount="3"/>


<!-- =============================================================== -->
<!-- Skip beginning dsig etc -->
<!-- =============================================================== -->

<xsl:template match="dsig:DigestValue">
</xsl:template>

<xsl:template match="dsig:SignatureValue">
</xsl:template>

<xsl:template match="dsig:X509Certificate">
</xsl:template>

<!-- =============================================================== -->
<!-- Root. Create the bone and call templates for each page sequence -->
<!-- =============================================================== -->

<!-- parameter - path to javascripts and css files-->
<xsl:param name="PATH_TO_JS" />
<xsl:param name="PATH_TO_CSS" />

<xsl:template match="fo:root">
	
  <html style="overflow:auto;">
    <head>
      <!-- title tag MUST be empty - used for communication -->
      <title></title>
      <META http-equiv="Content-Style-Type" content="text/css"/>
      <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />
      <style type="text/css">
        a { color: black; border: none; text-decoration: none; }
        img { border: none; }
      </style>
    
    	<!-- jQuery -->
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/jquery-1.4.1.min.js</xsl:attribute>
      </xsl:element>
      
      <!-- date picker -->
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/jquery-ui-1.8.5.custom.min.js</xsl:attribute>
      </xsl:element>
      
      <xsl:element name="link">
      	<xsl:attribute name="rel">stylesheet</xsl:attribute>
      	<xsl:attribute name="media">screen</xsl:attribute>
      	<xsl:attribute name="type">text/css</xsl:attribute>
      	<xsl:attribute name="href"><xsl:value-of select="$PATH_TO_CSS"/>/smoothness/jquery-ui-1.8.5.custom.css</xsl:attribute>
      </xsl:element>
      
      <!-- required plugins -->
            
      <!-- script with functions handling general ZFO stuff & css for zfo-->  
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/zfo_script.js</xsl:attribute>
      </xsl:element>
      
      <xsl:element name="link">
      	<xsl:attribute name="rel">stylesheet</xsl:attribute>
      	<xsl:attribute name="media">screen</xsl:attribute>
      	<xsl:attribute name="type">text/css</xsl:attribute>
      	<xsl:attribute name="href"><xsl:value-of select="$PATH_TO_CSS"/>/zfo.css</xsl:attribute>
      </xsl:element>  

      <!--<link rel="stylesheet" type="text/css" media="screen" href="../../styles/zfo.css"/>-->
      
      <!-- script with functions handling repeating sections -->  
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/repe_sections.js</xsl:attribute>
      </xsl:element>
      
      <!-- script with functions handling utf8 -->  
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/utf8.js</xsl:attribute>
      </xsl:element>
      
      <!-- script with helper functions -->  
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/helpers.js</xsl:attribute>
      </xsl:element>
      
      <!-- script that provides communication with gtk app -->
      <xsl:element name="script">
      	<xsl:attribute name="type">text/javascript</xsl:attribute>
      	<xsl:attribute name="src"><xsl:value-of select="$PATH_TO_JS"/>/ipc.js</xsl:attribute>
      </xsl:element>
  </head>
	
	<xsl:variable name="bgcolor" select="//fm:formparams/@proppapercolor" />
	<xsl:variable name="bgimage" select="//fo:region-body/@background-image" />
	
	<xsl:element name="body">
		<xsl:attribute name="background"><xsl:value-of select="$bgimage"/></xsl:attribute>
		<xsl:attribute name="style">background-color:<xsl:value-of select="$bgcolor" /></xsl:attribute>
		<xsl:attribute name="marginwidth">0</xsl:attribute>
		<xsl:attribute name="marginheight">0</xsl:attribute>
		<xsl:attribute name="leftmargin">0</xsl:attribute>
		<xsl:attribute name="topmargin">0</xsl:attribute>
    		
		<xsl:element name="div">
			<xsl:attribute name="id">main_area</xsl:attribute>
			<xsl:attribute name="style">height: 100%; width: 100%; margin-bottom: 1px;</xsl:attribute>
			
			<!-- reperesentation of pending request-->			
			<xsl:element name="div">      	
				<xsl:attribute name="id">pending_request</xsl:attribute>
			</xsl:element>
			
			<!-- zfo form name - identifier of the zfo document -->
			<xsl:element name="div">      	
				<xsl:attribute name="id">zfo_form</xsl:attribute>
				
				<xsl:apply-templates select="fo:page-sequence"/>

				<xsl:element name="div">
					<xsl:attribute name="style">display: none;</xsl:attribute>
					<xsl:attribute name="name">current_zfo_name</xsl:attribute>
					<xsl:attribute name="id">current_zfo_name</xsl:attribute>
				</xsl:element>
				
			</xsl:element>

    	</xsl:element> <!-- main area -->


	</xsl:element> <!-- body -->
  </html>

</xsl:template>

<!-- do not put formparams into output - they contain model and it is not needed in html-->
<xsl:template match="fm:form-params">
</xsl:template>

<xsl:template match="/dsig:Signature/dsig:Object[2]">
</xsl:template>

<!-- =============================================================== -->
<!-- fo:page-sequence. Draws a header before and a footer after.     -->
<!-- Sidebars are skipped: there's no way to rotate the text in HTML -->
<!-- and horizontal text in the sidebars eats up too much space.     -->
<!-- =============================================================== -->

<xsl:template match="fo:page-sequence">

    <xsl:variable name="current-master"><xsl:value-of select="@master-reference"/></xsl:variable>

    <!-- One page master will be used for the whole page sequence -->
    <xsl:variable name="page-master-name">
      <xsl:choose>
        <xsl:when test="../fo:layout-master-set/fo:simple-page-master[@master-name=$current-master]">
          <!-- specified master is a page master: use it -->
          <xsl:value-of select="$current-master"/>
        </xsl:when>
        <xsl:otherwise>
          <!-- specified master is a page sequence master: -->
          <!-- find master name for the first page -->
          <xsl:apply-templates select="../fo:layout-master-set/fo:page-sequence-master[@master-name=$current-master]"/>
        </xsl:otherwise>
      </xsl:choose>
    </xsl:variable>

    <!-- Load the page master into a variable. No problem if it's null; should work the same ;-) -->
    <xsl:variable name="page-master" select="../fo:layout-master-set/fo:simple-page-master[@master-name=$page-master-name]"/>


    <!-- Start real drawing -->
    <br/>  <!-- make an offset before each page sequence -->

    <!-- Header -->
    <xsl:variable name="header-region" select="$page-master/fo:region-before"/>
    <xsl:apply-templates select="fo:static-content[@flow-name = $header-region/@region-name
                              or (@flow-name='xsl-region-before' and not($header-region/@region-name))]">
      <xsl:with-param name="region" select="$header-region"/>
    </xsl:apply-templates>

    <!-- Body -->
    <xsl:apply-templates select="fo:flow">
      <xsl:with-param name="region" select="$page-master/fo:region-body"/>
    </xsl:apply-templates>

    <!-- Footer -->
    <xsl:variable name="footer-region" select="$page-master/fo:region-after"/>
    <xsl:apply-templates select="fo:static-content[@flow-name = $footer-region/@region-name
                              or (@flow-name='xsl-region-after' and not($footer-region/@region-name))]">
      <xsl:with-param name="region" select="$footer-region"/>
    </xsl:apply-templates>

    <br/>  <!-- make an offset after each page sequence -->

</xsl:template>

<!-- =============================================================== -->
<!-- fo:block                                                        -->
<!-- =============================================================== -->

<xsl:template match="fo:block|fo:block-container">
  &anchor;<div>&add-style;&add-onclick;<xsl:apply-templates mode="check-for-pre"/></div>
</xsl:template>

<!-- 
form602 repetition block. 
Two buttons for adding/deleting repeating block
are added.
-->
<xsl:template match="fo:block[@form602ct='rep']|fo:block-container[@form602ct='rep']">
<div name="div-rep-container">
  &anchor;<div>&add-style;&add-onclick;
	<xsl:attribute name="name"><xsl:value-of select="@form602repe"/>-rep-0</xsl:attribute>
	<xsl:attribute name="class">602-repetition-block</xsl:attribute>
	<button onclick="clone_div(this.parentNode)" type="button">+</button>
	<button onclick="del_div(this.parentNode)" type="button">-</button>
	<br />
	<xsl:apply-templates mode="check-for-pre"/></div>
</div>
</xsl:template>

<!-- =============================================================== -->
<!-- fo:inline-sequence                                              -->
<!-- =============================================================== -->

<xsl:template match="fo:inline | fo:wrapper">
  &anchor;<span>&add-style;&add-onclick;<xsl:apply-templates/></span>
</xsl:template>

<!-- =============================================================== -->
<!-- fo:list-block                                                   -->
<!-- =============================================================== -->

<xsl:template match="fo:list-block">
  <xsl:variable name="label-separation">
    <xsl:choose>
      <xsl:when test="@provisional-label-separation">
        <xsl:apply-templates select="@provisional-label-separation"
                             mode="convert-to-pixels"/>
      </xsl:when>
      <xsl:otherwise>8</xsl:otherwise>
    </xsl:choose>
  </xsl:variable>

  <xsl:variable name="body-offset">
    <xsl:choose>
      <xsl:when test="@provisional-distance-between-starts">
        <xsl:apply-templates select="@provisional-distance-between-starts"
                             mode="convert-to-pixels"/>
      </xsl:when>
      <xsl:otherwise>32</xsl:otherwise>
    </xsl:choose>
  </xsl:variable>

  <div>&add-style;
    &anchor;
    <table width="100%">
      <xsl:apply-templates select="fo:list-item | fo:list-item-label">
        <xsl:with-param name="label-width"
                        select="$body-offset - $label-separation"/>
        <xsl:with-param name="gap-width"
                        select="$label-separation"/>
      </xsl:apply-templates>
    </table>
  </div>

</xsl:template>

<!-- =============================================================== -->
<!-- fo:list-item                                                    -->
<!-- =============================================================== -->

<xsl:template match="fo:list-item">
  <xsl:param name="label-width"/>
  <xsl:param name="gap-width"/>

  <tr>&add-style;
    <xsl:apply-templates select="fo:list-item-label" mode="draw-cell">
       <xsl:with-param name="width" select="$label-width"/>
    </xsl:apply-templates>
    <xsl:if test="$gap-width &gt; 0">
      <td width="{$gap-width}">&#160;</td>
    </xsl:if>

    <xsl:apply-templates select="fo:list-item-body" mode="draw-cell"/>
  </tr>
</xsl:template>

<!-- =============================================================== -->
<!-- fo:list-item-label - itemless lists                             -->
<!-- =============================================================== -->

<xsl:template match="fo:list-block/fo:list-item-label">
  <xsl:param name="label-width"/>
  <xsl:param name="gap-width"/>

  <tr>
    <xsl:apply-templates select="." mode="draw-cell">
       <xsl:with-param name="width" select="$label-width"/>
    </xsl:apply-templates>
    <xsl:if test="$gap-width &gt; 0">
      <td width="{$gap-width}">&#160;</td>
    </xsl:if>

    <xsl:apply-templates select="following-sibling::fo:list-item-body[1]" mode="draw-cell"/>
  </tr>
</xsl:template>


<!-- =============================================================== -->
<!-- fo:list-item-body - itemless lists                              -->
<!-- =============================================================== -->

<xsl:template match="fo:list-item-label | fo:list-item-body" mode="draw-cell">
  <xsl:param name="width" select="'auto'"/>
  <td valign="top">&add-style;&anchor;
    <xsl:if test="$width != 'auto'">
      <xsl:attribute name="width">
        <xsl:value-of select="$width"/>
      </xsl:attribute>
    </xsl:if>

    <xsl:apply-templates mode="check-for-pre"/>
  </td>
</xsl:template>


<!-- =============================================================== -->
<!-- fm:internal-graphic - embedd base64 content into browser. 
Works problably only in Firefox. Jpeg mimetype is only a guess, might not work.-->                 
<!-- =============================================================== -->
<xsl:template match="//fm:internal-graphic" >
<xsl:element name="img">
	<xsl:attribute name="src"><xsl:value-of select="concat('data:image/jpg;base64,', text())" /> </xsl:attribute>
	<xsl:attribute name="alt"><xsl:value-of select="@imghsh" /> </xsl:attribute>	
</xsl:element>

</xsl:template>



<!-- =============================================================== -->
<!-- fo:table and its components                                     -->
<!-- =============================================================== -->

<xsl:template match="fo:table">
  &anchor;
  <table>&add-style;
    <xsl:if test="not(@display-align)">
      <xsl:attribute name="valign">top</xsl:attribute>
    </xsl:if>
		
    <xsl:apply-templates/>
  </table>
</xsl:template>

<!-- 
form602 repetition table.
Two buttons for adding/deleting repeating table
are added.
-->
<xsl:template match="fo:table[@form602ct='rep']">
  <div name="table-rep-container">
  &anchor;
  <table>&add-style;
    <xsl:if test="not(@display-align)">
      <xsl:attribute name="valign">top</xsl:attribute>
    </xsl:if>
    <xsl:attribute name="name"><xsl:value-of select="@form602repe"/>-rep-0</xsl:attribute>
	<xsl:attribute name="class">602-repetition-table</xsl:attribute>
	<tbody>
		<tr> 
		<td><button onclick="clone_table(this.parentNode.parentNode.parentNode.parentNode)" type="button">+</button> 
			<button onclick="del_table(this.parentNode.parentNode.parentNode.parentNode)" type="button">-</button> 
			</td>
		</tr> 
	</tbody>
    <xsl:apply-templates/>
  </table>
  </div>
</xsl:template>

<!-- set table column widths -->
<xsl:template match="fo:table-column">
	<xsl:choose>	
		<xsl:when test="@column-width">
			<xsl:variable name="cw" select="@column-width"/>
      	<col style="width: {$cw};"/>
   	</xsl:when>
   	<xsl:otherwise>
   		<col style="width: auto;"/>
   	</xsl:otherwise>
   </xsl:choose>
</xsl:template>

<xsl:template match="fo:table-header">
  <thead>&add-style;
    <xsl:apply-templates/>
  </thead>
</xsl:template>

<xsl:template match="fo:table-footer">
  <tfoot>&add-style;
    <xsl:apply-templates/>
  </tfoot>
</xsl:template>

<xsl:template match="fo:table-body">
  <tbody>&add-style;
    <xsl:apply-templates/>
  </tbody>
</xsl:template>

<xsl:template match="fo:table-row">
  <tr>&add-style;
    <xsl:apply-templates mode="display"/>
  </tr>
</xsl:template>

<!-- 
form602 repetition rows.
Each row could be repeated in the form.
To the row, there are two more buttons + and - to add/delete
row (or other repeating section) 
-->
<xsl:template match="fo:table-row[@form602ct='rep']">	
  <xsl:variable name="repe_att"><xsl:value-of select="@form602repe"/></xsl:variable>  
  <tr name="{$repe_att}-rep-0" class="602-repetition-row">&add-style;
    	<xsl:apply-templates mode="display"/>
		<td ><button onclick="clone_row(this.parentNode.parentNode, this)" type="button">+</button></td>
		<td ><button onclick="del_row(this.parentNode.parentNode)" type="button">-</button></td>
  </tr>
</xsl:template>

<xsl:template match="fo:table-cell" mode="display">
  <td>&add-style;	&add-onclick;
    <xsl:if test="not(@display-align)">
      <xsl:attribute name="valign">top</xsl:attribute>
    </xsl:if>
    <xsl:apply-templates select="@*" mode="get-table-attributes"/>
    <xsl:apply-templates mode="check-for-pre"/>	
  </td>
</xsl:template>

<!--  
In next part of xslt, the table cells are processed.
If table cell have 'content type' attribute, there is also
a template matching the same content type in 
other formatting object (block, inline, table-cells...).
(Templates are grouped by FO content type, not by FO 'name') 
-->

<!--   
General text input
-->
<xsl:template match="fo:table-cell[@form602ct='txt']" mode="display">
  <td>&add-style;&add-onclick;
   <xsl:apply-templates select="@*" mode="get-table-attributes"/>
   <xsl:apply-templates mode="check-for-pre"/>	 

	<xsl:call-template name="text-input" />	

   <xsl:apply-templates/>

  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='txt']|fo:block-container[@form602ct='txt']" >
  <div>&add-style;&add-onclick;  
   <xsl:apply-templates mode="check-for-pre"/>
	<xsl:call-template name="text-input" />
   <xsl:apply-templates/>

  </div>
</xsl:template>


<xsl:template match="fo:inline[@form602ct='txt']">
	<span>&add-style;&add-onclick;
	<xsl:call-template name="text-input" />
		
	</span>
</xsl:template>

<!-- input for general text value -->
<xsl:template name="text-input">	
	<xsl:element name="input" >
		<xsl:attribute name="type">text</xsl:attribute>
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip"/></xsl:attribute>
		<xsl:attribute name="maxlength"><xsl:value-of select="@form602maxchars"/></xsl:attribute>	
		<xsl:attribute name="name"><xsl:value-of select="@form602bind" /></xsl:attribute>		
	</xsl:element>
	
</xsl:template>

<!-- "out" value - noneditable information -->
<xsl:template match="fo:block[@form602ct='out']" >
  <div>&add-style;&add-onclick;  
   <xsl:attribute name="id"><xsl:value-of select="@form602bindref" /></xsl:attribute>
	 <xsl:attribute name="class">outputelement</xsl:attribute>
	 <xsl:attribute name="name"><xsl:value-of select="@form602bindref" /></xsl:attribute>
	 
	 <xsl:apply-templates mode="check-for-pre"/>	 
   <xsl:apply-templates/>
  </div>
</xsl:template>

<xsl:template match="fo:inline[@form602ct='out']">
	<span>&add-style;&add-onclick;
		<xsl:attribute name="id"><xsl:value-of select="@form602bindref" /></xsl:attribute>
		<xsl:attribute name="class">outputelement</xsl:attribute>
		<xsl:attribute name="name"><xsl:value-of select="@form602bindref" /></xsl:attribute>		
	</span>
</xsl:template>

<!--   
Number input. Format of a number 
is specified as optional minus sign,
number 0-9, then optional decimal delimiter , or .
and if there was a delimiter, at least one number 0-9. 
-->
<xsl:template match="fo:table-cell[@form602ct='num']" mode="display">
  <td>&add-style;&add-onclick;
   <xsl:apply-templates select="@*" mode="get-table-attributes"/>
   <!--<xsl:apply-templates mode="check-for-pre"/>	 -->

	<xsl:call-template name="num-input" />	

  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='num']|fo:block-container[@form602ct='num']" >
  <div>&add-style;&add-onclick;   
	<xsl:call-template name="num-input" />

  </div>
</xsl:template>

<xsl:template match="fo:inline[@form602ct='num']">
	<span>&add-style;&add-onclick;
	<xsl:call-template name="num-input" />
		
	</span>
</xsl:template>

<!-- number format is checked in onchange event handler -->
<xsl:template name="num-input">	
	<xsl:element name="input" >
		<xsl:attribute name="type">text</xsl:attribute>
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip"/></xsl:attribute>			
		<xsl:attribute name="maxlength"><xsl:value-of select="@form602maxchars"/></xsl:attribute>	
		<xsl:attribute name="name"><xsl:value-of select="@form602bind" /></xsl:attribute>
		<!-- class is used to identify number input -->
		<xsl:attribute name="class">numberInput</xsl:attribute>
		<xsl:attribute name="onkeyup">number_input_keypress_handler(this)</xsl:attribute>		
	</xsl:element>
	
</xsl:template>


<!--   
File input
-->
<xsl:template match="fo:table-cell[@form602ct='bin']" mode="display">
  <td>&add-style;&add-onclick;
   <xsl:apply-templates select="@*" mode="get-table-attributes"/>
   <!--<xsl:apply-templates mode="check-for-pre"/>	 -->
	<xsl:call-template name="file-input" />
  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='bin']|fo:block-container[@form602ct='bin']" mode="display">
  <div>&add-style;   
	<xsl:call-template name="file-input"/>
		
  </div>
</xsl:template>

<xsl:template match="fo:inline[@form602ct='bin']" mode="display">
  <span>&add-style;   
	<xsl:call-template name="file-input" />
		
  </span>
</xsl:template>

<xsl:template name="file-input">	
	<xsl:element name="div">
		<xsl:attribute name="name">file-input-container</xsl:attribute>
		
		<xsl:element name="input" >		
			<xsl:attribute name="type">text</xsl:attribute>
			<xsl:attribute name="class">fileInput</xsl:attribute>
			<xsl:attribute name="readonly">readonly</xsl:attribute>
			<xsl:attribute name="title"><xsl:value-of select="@form602tooltip"/></xsl:attribute>		
			<xsl:attribute name="maxlength"><xsl:value-of select="@form602maxchars"/></xsl:attribute>
			<xsl:attribute name="accept"><xsl:value-of select="@form602ffilter"/></xsl:attribute>		
			<xsl:attribute name="onclick"></xsl:attribute>
			<xsl:attribute name="name"><xsl:value-of select="@form602bind" /></xsl:attribute>
		</xsl:element>
		
		<xsl:element name="button">
			<xsl:attribute name="name">add_attachment</xsl:attribute>
			Vybrat
		</xsl:element>
		
		<xsl:element name="button">
			<xsl:attribute name="name">del_attachment</xsl:attribute>
			Smazat
		</xsl:element>
		
	</xsl:element>
</xsl:template>

<!--  
Listbox input
-->

<!-- draws dropdown listbox -->
<xsl:template name="draw-list">
	<xsl:param name="items"/>
	<xsl:variable name="counter" select="1"/>				
	<xsl:for-each select="$items">
		<option>
			<xsl:attribute name="value"><xsl:value-of select="@value"/></xsl:attribute>			
			<xsl:value-of select="@itemtext"/>
		</option>
	</xsl:for-each> 

</xsl:template>


<!--
Creates listbox with specified name and adds onchange event if required
-->
<xsl:template name="create-select">
	<xsl:param name="list-name" />
	<xsl:param name="list-bindname" />	
	<xsl:param name="tooltip" />
	<select>	
		<xsl:attribute name="title"><xsl:value-of select="$tooltip"/> </xsl:attribute>		
		<xsl:attribute name="style">width: 100%;</xsl:attribute>  
		
		<!-- add onchange event if this listbox controls some multiswitch 
		<xsl:call-template name="add-602ilb-onchange-attribute">				
			<xsl:with-param name="list-bindname" select="@form602bind"/>	
		</xsl:call-template>-->
		<xsl:attribute name="name"><xsl:value-of select="@form602bind" /></xsl:attribute>
		<!--<xsl:variable name="list-name" select="@form602listname"/>-->
		<xsl:call-template name="draw-list">		
			<xsl:with-param name="items" select="//fm:enum[@name=$list-name]/fm:list/fm:listitem"/>	
		</xsl:call-template>
	</select>	
</xsl:template>


<xsl:template match="fo:table-cell[@form602ct='ilb']|fo:table-cell[@form602ct='lbx']" mode="display">
  <td>&add-style;&add-onclick;
  	<xsl:apply-templates select="@*" mode="get-table-attributes"/>
	<xsl:call-template name="create-select">		
		<xsl:with-param name="list-name" select="@form602listname"/>
		<xsl:with-param name="list-bindname" select="@form602bindref"/>		
		<xsl:with-param name="tooltip" select="@form602tooltip" />
	</xsl:call-template>
   
  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='ilb']|fo:block-container[@form602ct='ilb']
							|fo:block[@form602ct='lbx']|fo:block-container[@form602ct='lbx']" >
  <div>&add-style;&add-onclick;   
   <!--<xsl:apply-templates mode="check-for-pre"/>-->	 

	<xsl:call-template name="create-select">		
		<xsl:with-param name="list-name" select="@form602listname"/>
		<xsl:with-param name="list-bindname" select="@form602bindref"/>		
		<xsl:with-param name="tooltip" select="@form602tooltip" />
	</xsl:call-template>
   
  </div>
</xsl:template>

<xsl:template match="fo:inline[@form602ct='ilb']|fo:inline[@form602ct='lbx']" >
  <span>&add-style;&add-onclick;   
   <xsl:call-template name="create-select">		
		<xsl:with-param name="list-name" select="@form602listname"/>
		<xsl:with-param name="list-bindname" select="@form602bindref"/>		
		<xsl:with-param name="tooltip" select="@form602tooltip" />
	</xsl:call-template>
   
  </span>
</xsl:template>


<!--   
Date input
-->
<xsl:template match="fo:table-cell[@form602ct='dat']" mode="display">
  <td>&add-style;&add-onclick;
   <xsl:apply-templates select="@*" mode="get-table-attributes"/>
   
	<xsl:call-template name="date-input"/>		
		
  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='dat']|fo:block-container[@form602ct='dat']" >
  <div>&add-style;&add-onclick;
  
	<xsl:call-template name="date-input" />
    
  </div>
</xsl:template>

<xsl:template match="fo:inline[@form602ct='dat']" >
  <span>&add-style;&add-onclick;
    
	<xsl:call-template name="date-input"/>		
	
  </span>
</xsl:template>

<xsl:template name="date-input">	
	<xsl:element name="input" >
		<xsl:attribute name="type">text</xsl:attribute>
		<xsl:attribute name="id">date</xsl:attribute>
		<xsl:attribute name="class">date-pick</xsl:attribute>
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip"/></xsl:attribute>
		<xsl:attribute name="name"><xsl:value-of select="@form602bind" /></xsl:attribute>
		<xsl:attribute name="readonly">readonly</xsl:attribute>
	</xsl:element>	
	
</xsl:template>

<!--  
Button input
-->
<xsl:template match="fo:table-cell[@form602ct='btn']" mode="display">
  <td>&add-style; <!-- onclick is handled by form602btnclick -->
   <xsl:apply-templates select="@*" mode="get-table-attributes"/>
   <!--<xsl:apply-templates mode="check-for-pre"/>	 -->
	
	<xsl:call-template name="button-input" />		
	
  </td>
</xsl:template>

<xsl:template match="fo:block[@form602ct='btn']|fo:block-container[@form602ct='btn']|fo:inline[@form602ct='btn']">
  <div>&add-style; <!-- onclick is handled by form602btnclick -->   
   <!--<xsl:apply-templates mode="check-for-pre"/>	 -->
	<xsl:call-template name="button-input" />		
	
  </div>
</xsl:template>

<xsl:template name="button-input">		
	<!-- we have to escape backslashes used in Windows paths-->
	<xsl:variable name="not-escaped"><xsl:value-of select="@form602btnclick"/></xsl:variable>
	<xsl:variable name="escaped"><xsl:call-template name="replace-string">
          <xsl:with-param name="text" select="$not-escaped"/>
          <xsl:with-param name="replace">\</xsl:with-param> 
          <xsl:with-param name="with">\\</xsl:with-param> 
        </xsl:call-template></xsl:variable>
        
	<xsl:element name="button" >
		<xsl:attribute name="type">button</xsl:attribute>
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip" /></xsl:attribute>		
		<xsl:attribute name="onclick">perform_click_action(this)</xsl:attribute>
		<xsl:attribute name="value"><xsl:value-of select="@form602btnclick" /></xsl:attribute>
		<xsl:attribute name="zfo-app-event"><xsl:value-of select="@form602btnclick" /></xsl:attribute>
		
		 <xsl:apply-templates />
	</xsl:element>	
</xsl:template>

<xsl:template name="replace-string">
    <xsl:param name="text"/>
    <xsl:param name="replace"/>
    <xsl:param name="with"/>
    <xsl:choose>
      <xsl:when test="contains($text,$replace)">
        <xsl:value-of select="substring-before($text,$replace)"/>
        <xsl:value-of select="$with"/>
        <xsl:call-template name="replace-string">
          <xsl:with-param name="text"
select="substring-after($text,$replace)"/>
          <xsl:with-param name="replace" select="$replace"/>
          <xsl:with-param name="with" select="$with"/>
        </xsl:call-template>
      </xsl:when>
      <xsl:otherwise>
        <xsl:value-of select="$text"/>
      </xsl:otherwise>
</xsl:choose>
</xsl:template>

<!-- 
Radio input
-->
<xsl:template match="//fo:character[@form602ct='rad']">
  <span>&add-onclick;
	<xsl:call-template name="radio-input"/>
  </span>
		
</xsl:template>

<xsl:template name="radio-input">	
	<xsl:element name="input" >
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip" /></xsl:attribute>
		<xsl:attribute name="type">radio</xsl:attribute>		
		<xsl:attribute name="name"><xsl:value-of select="@form602radio" /></xsl:attribute>	
		<xsl:attribute name="id"><xsl:value-of select="concat(@form602radio, '-',@form602on)" /></xsl:attribute>
		<xsl:attribute name="value"><xsl:value-of select="@form602on" /></xsl:attribute>	
				
		<xsl:apply-templates />
	</xsl:element>
	
</xsl:template>

<!--   
Checkbox input
-->
<xsl:template match="//fo:character[@form602ct='chb']">
  <span>&add-onclick;
	<xsl:call-template name="checkbox-input"/>
	</span>
</xsl:template>

<xsl:template name="checkbox-input">	
	<xsl:element name="input" >
		<xsl:attribute name="title"><xsl:value-of select="@form602tooltip" /></xsl:attribute>
		<xsl:attribute name="type">checkbox</xsl:attribute>
		<xsl:attribute name="id"><xsl:value-of select="@form602chkbx" /></xsl:attribute>
		<xsl:attribute name="name"><xsl:value-of select="@form602chkbx" /></xsl:attribute>		
		<xsl:apply-templates />
	</xsl:element>
	
</xsl:template>



<xsl:template match="fo:table-cell" priority="-1">&add-onclick;
	<xsl:apply-templates />
</xsl:template>



<!-- This template accounts for "rowless" tables -->
<xsl:template priority="1"
              match="fo:table-cell[not(parent::fo:table-row)]
              [not(preceding-sibling::fo:table-cell) or @starts-row='true'
               or preceding-sibling::fo:table-cell[1][@ends-row='true']]">
  <tr>
    <xsl:call-template name="enumerate-rowless-cells"/>
  </tr>
</xsl:template>

<xsl:template name="enumerate-rowless-cells">
  <xsl:apply-templates select="." mode="display"/>
  <xsl:if test="not(@ends-row='true')">
    <xsl:for-each select="following-sibling::fo:table-cell[1]
                          [not(@starts-row='true')]">
      <xsl:call-template name="enumerate-rowless-cells"/>
    </xsl:for-each>
  </xsl:if>
</xsl:template>


<!-- =============================================================== -->
<!-- fo:multi-switch   -->
<!-- =============================================================== -->
<xsl:template match="//fo:multi-switch">
	<xsl:variable name="data-switch-bindref" select="fm:data-switch[@form602bindref]"/>
	<div>
		<xsl:apply-templates />
	</div>
</xsl:template>


<!-- =============================================================== -->
<!-- fo:multi-case  - put it into div, then use jquery to hide/show them  -->
<!-- =============================================================== -->
<xsl:template match="//fo:multi-case">
	<xsl:variable name="id" select="@case-name"/>
	<xsl:variable name="bindref" select="../fm:data-switch/@form602bindref"/>	
	
	<div>			
		<xsl:variable name="div-id" select="concat('div-', $bindref, '-', $id)" />
		<xsl:attribute name="name">
			<xsl:value-of select="$div-id"/>
		</xsl:attribute>
		
		<xsl:apply-templates />
	</div>
	
	
</xsl:template>

<!-- =============================================================== -->
<!-- fo:inline-graphic                                               -->
<!-- =============================================================== -->

<xsl:template match="fo:external-graphic">
  <xsl:variable name="cleaned-url">
    <xsl:apply-templates select="@src" mode="unbracket-url"/>
  </xsl:variable>
  &anchor;<img src="{$cleaned-url}"><xsl:apply-templates select="@height|@width|@*[starts-with(name(),'border')]"/></img>
</xsl:template>

<!-- =============================================================== -->
<!-- fo:basic-link                                                  -->
<!-- =============================================================== -->

<xsl:template match="fo:basic-link[@external-destination]">

  <xsl:variable name="cleaned-url">
    <xsl:apply-templates select="@external-destination" mode="unbracket-url"/>
  </xsl:variable>

  &anchor;<a href="{$cleaned-url}">&add-style;<xsl:apply-templates/></a>
</xsl:template>

<xsl:template match="fo:basic-link[@internal-destination]">
  &anchor;<a href="#{@internal-destination}">&add-style;<xsl:apply-templates/></a>
</xsl:template>


<!-- =============================================================== -->
<!-- fo:marker/fo:retrieve-marker                                    -->
<!-- =============================================================== -->

<xsl:template match="fo:marker"/>
<xsl:template match="fo:marker" mode="retrieve-marker">
  <xsl:apply-templates/>
</xsl:template>

<xsl:template match="fo:retrieve-marker">

  <xsl:variable name="class-name" select="@retrieve-class-name"/>
  <xsl:variable name="matching-markers"
                select="ancestor::fo:page-sequence/descendant::fo:marker[@marker-class-name=$class-name]"/>

  <xsl:choose>
    <xsl:when test="@retrieve-position='last-starting-within-page'
                 or @retrieve-position='last-ending-within-page'">
      <xsl:apply-templates select="$matching-markers[position()=last()]" mode="retrieve-marker"/>
    </xsl:when>
    <xsl:otherwise>
      <xsl:apply-templates select="$matching-markers[1]" mode="retrieve-marker"/>
    </xsl:otherwise>
  </xsl:choose>

</xsl:template>


<!-- 
Onchange events for listbox, radio and checkbox.
Handlers control showing/hiding of multicases 
that are bound to the html control element 
(select, check or radiobox).
--> 

<!--
form 602 listbox should have onchange event. It may control some fo:multi-switch. If there
exists a fo:multi-switch that has a fm:data-switch element with form602bindref attribute equal to the form602bind
attribute of a listbox (ilb), then this listbox "controls" which [art of multiswitch will be drawn to the output.
-->
<xsl:template name="add-602ilb-onchange-attribute">
	<xsl:param name="list-bindname" />
	<!-- all nodes fo:multi-switch/fm:data-switch -->
	<xsl:variable name="multiswitch-list" select="//fo:multi-switch/fm:data-switch" />
	<xsl:for-each select="$multiswitch-list">	
		<xsl:variable name="bindref-name" select="@form602bindref" />
		<!--multiswitch bindref-<xsl:value-of select="$bindref-name"/>		
		list binding-<xsl:value-of select="$my-bind"/>		-->
		<xsl:if test="$list-bindname = $bindref-name">
			<xsl:attribute name="onchange">
				show_div_selected_option(this)
			</xsl:attribute>
		</xsl:if>
	</xsl:for-each>
</xsl:template>

<xsl:template name="add-602radio-onclick-attribute">
	<xsl:param name="radio-bindname" />
	<!-- all nodes fo:multi-switch/fm:data-switch -->
	<xsl:variable name="multiswitch-list" select="//fo:multi-switch/fm:data-switch" />
	<xsl:for-each select="$multiswitch-list">	
		<xsl:variable name="bindref-name" select="@form602bindref" />
		<!--multiswitch bindref-<xsl:value-of select="$bindref-name"/>		
		list binding-<xsl:value-of select="$my-bind"/>		-->
		<xsl:if test="$radio-bindname = $bindref-name">
			<xsl:attribute name="onclick">
				show_div_selected_radio(this)
			</xsl:attribute>
		</xsl:if>
	</xsl:for-each>
</xsl:template>


<xsl:template name="add-602cbox-onclick-attribute">
	<xsl:param name="cbox-bindname" />
	<!-- all nodes fo:multi-switch/fm:data-switch -->
	<xsl:variable name="multiswitch-list" select="//fo:multi-switch/fm:data-switch" />
	<xsl:for-each select="$multiswitch-list">	
		<xsl:variable name="bindref-name" select="@form602bindref" />
		<!--multiswitch bindref-<xsl:value-of select="$bindref-name"/>		
		list binding-<xsl:value-of select="$my-bind"/>		-->
		<xsl:if test="$cbox-bindname = $bindref-name">
			<xsl:attribute name="onclick">
				show_div_selected_cbox(this)
			</xsl:attribute>
		</xsl:if>
	</xsl:for-each>
</xsl:template>




<!-- *************************************************************** -->
<!-- Treatment of attributes that are either identical to their CSS1 -->
<!-- counterparts, of find an equivalent expression there            -->

<!-- =============================================================== -->
<!-- Default rule: copy CSS1 attributes and suppress all other       -->
<!-- =============================================================== -->

<xsl:template match="@*" priority="-2" mode="collect-style-attributes"/>

<xsl:template match="@color |
                     @background |
                     @background-color |                     
                     @background-position |
                     @background-repeat |
                     @padding |
                     @padding-top |
                     @padding-bottom |
                     @padding-right |
                     @padding-left |
                     @margin |
                     @margin-top |
                     @margin-bottom |
                     @margin-right |
                     @margin-left |
                     @border |
                     @border-top |
                     @border-bottom |
                     @border-right |
                     @border-left |
                     @border-width |
                     @border-top-width |
                     @border-bottom-width |
                     @border-right-width |
                     @border-left-width |
                     @border-color |
                     @border-top-color |
                     @border-bottom-color |
                     @border-right-color |
                     @border-left-color |
                     @border-style |
                     @border-top-style |
                     @border-bottom-style |
                     @border-right-style |
                     @border-left-style |
                     @letter-spacing |
                     @word-spacing |
                     @line-height |
                     @font |
                     @font-family |
                     @font-size |
                     @font-weight |
                     @font-style |
                     @font-variant |
                     @vertical-align |
                     @text-decoration |
                     @text-indent |
                     @text-transform|
                     @height"
                     mode="collect-style-attributes">
  <xsl:value-of select="name()"/>
  <xsl:text>: </xsl:text>
  <xsl:value-of select="."/>
  <xsl:text>; </xsl:text>
</xsl:template>


<!-- =============================================================== -->
<!-- Some attributes deserve special treatment -->

<xsl:template match="@text-align" mode="collect-style-attributes">
  <xsl:text>text-align: </xsl:text>
  <xsl:choose>
    <xsl:when test=".='start' or .='inside'">left</xsl:when>
    <xsl:when test=".='end' or .='outside'">right</xsl:when>
    <xsl:otherwise><xsl:value-of select="."/></xsl:otherwise>
  </xsl:choose>
  <xsl:text>; </xsl:text>
</xsl:template>

<!-- =============================================================== -->
<!-- Handling writing-mode in borders, padding, and margins          -->
<!-- This version presumes lr-tb writing mode only.                  -->

<xsl:template match="@space-before.optimum |
                     @space-before [not (../@space-before.optimum)] |
                     @space-before.minimum [not (../@space-before.optimum) and not (../@space-before)] |
                     @space-before.maximum [not (../@space-before.optimum) and not (../@space-before) and not (../@space-before.minimum)] |
                     @space-after.optimum |
                     @space-after [not (../@space-after.optimum)] |
                     @space-after.minimum [not (../@space-after.optimum) and not (../@space-after)] |
                     @space-after.maximum [not (../@space-after.optimum) and not (../@space-after) and not (../@space-after.minimum)] |
                     @space-start.optimum |
                     @space-start [not (../@space-start.optimum)] |
                     @space-start.minimum [not (../@space-start.optimum) and not (../@space-start)] |
                     @space-start.maximum [not (../@space-start.optimum) and not (../@space-start) and not (../@space-start.minimum)] |
                     @space-end.optimum |
                     @space-end [not (../@space-end.optimum)] |
                     @space-end.minimum [not (../@space-end.optimum) and not (../@space-end)] |
                     @space-end.maximum [not (../@space-end.optimum) and not (../@space-end) and not (../@space-end.minimum)] |
                     @start-indent[not(parent::fo:list-item-body)] |
                     @end-indent[not(parent::fo:list-item-label)] |
                     @padding-before |
                     @padding-before.length |
                     @margin-before |
                     @border-before |
                     @border-before-width |
                     @border-before-width.length |
                     @border-before-color |
                     @border-before-style |
                     @padding-after |
                     @padding-after.length |
                     @margin-after |
                     @border-after |
                     @border-after-width |
                     @border-after-width.length |
                     @border-after-color |
                     @border-after-style |
                     @padding-start |
                     @padding-start.length |
                     @margin-start |
                     @border-start |
                     @border-start-width |
                     @border-start-width.length |
                     @border-start-color |
                     @border-start-style |
                     @padding-end |
                     @padding-end.length |
                     @margin-end |
                     @border-end |
                     @border-end-width |
                     @border-end-width.length |
                     @border-end-color |
                     @border-end-style"
              mode="collect-style-attributes">

  <xsl:variable name="property">
    <xsl:choose>
      <xsl:when test="starts-with(name(), 'border')">border</xsl:when>
      <xsl:when test="starts-with(name(), 'padding')">padding</xsl:when>
      <xsl:when test="starts-with(name(), 'margin')">margin</xsl:when>
      <xsl:when test="starts-with(name(), 'space')">margin</xsl:when>
      <xsl:when test="contains(name(), '-indent')">margin</xsl:when>
    </xsl:choose>
  </xsl:variable>

  <xsl:variable name="side">
    <xsl:choose>
      <xsl:when test="contains(name(), '-before') or contains(name(), '-top')">-top</xsl:when>
      <xsl:when test="contains(name(), '-after') or contains(name(), '-bottom')">-bottom</xsl:when>
      <xsl:when test="contains(name(), '-start') or starts-with(name(), 'start-') or contains(name(), '-left')">-left</xsl:when>
      <xsl:when test="contains(name(), '-end') or starts-with(name(), 'end-') or contains(name(), '-right')">-right</xsl:when>
    </xsl:choose>
  </xsl:variable>

  <xsl:variable name="parameter">
    <xsl:choose>
      <xsl:when test="contains(name(), '-width')">-width</xsl:when>
      <xsl:when test="contains(name(), '-color')">-color</xsl:when>
      <xsl:when test="contains(name(), '-style')">-style</xsl:when>
    </xsl:choose>
  </xsl:variable>

  <xsl:value-of select="concat($property, $side, $parameter)"/>
  <xsl:text>: </xsl:text>
  <xsl:value-of select="."/>
  <xsl:text>; </xsl:text>
</xsl:template>

<xsl:template match="*" mode="check-for-pre" priority="-1">
  <xsl:apply-templates select="."/>
</xsl:template>

<xsl:template match="*[@white-space-collapse='false'
                    or @linefeed-treatment='preserve'
                    or @wrap-option='no-wrap'
                    or @white-space='pre']"
                     mode="check-for-pre">
  <pre><xsl:apply-templates select="."/></pre>
</xsl:template>

<!-- =============================================================== -->
<!-- Recalculate a length to pixels. 1 in = 96 px, 1 em = 1 pc;      -->
<!-- this gives reasonable results for 800x600 and 1024x768 screens  -->
<!-- =============================================================== -->

<xsl:template match="@*" mode="convert-to-pixels">
  <xsl:variable name="scaling-factor">
    <xsl:choose>
      <xsl:when test="contains (., 'pt')">1.33</xsl:when>
      <xsl:when test="contains (., 'px')">1</xsl:when>
      <xsl:when test="contains (., 'pc')">16</xsl:when>
      <xsl:when test="contains (., 'in')">96</xsl:when>
      <xsl:when test="contains (., 'cm')">37.8</xsl:when>
      <xsl:when test="contains (., 'mm')">3.78</xsl:when>
      <xsl:when test="contains (., 'em')">16</xsl:when> <!-- guess: 1em = 12pt -->
      <xsl:otherwise>1</xsl:otherwise>
    </xsl:choose>
  </xsl:variable>

  <xsl:variable name="numeric-value"
       select="translate (., '-0123456789.ptxcinme', '-0123456789.')"/>
  <xsl:value-of select="$numeric-value * $scaling-factor"/>
</xsl:template>

<!-- =============================================================== -->
<!-- Remove brackets & quotes around URLs                            -->
<!-- =============================================================== -->

<xsl:template match="@*" mode="unbracket-url">
  <xsl:variable name="href" select="normalize-space(.)"/>

  <xsl:choose>
    <xsl:when test="(starts-with($href, 'url(') or starts-with($href, 'url ('))
                     and substring ($href, string-length($href)) = ')'">
      <!-- Remove 'url' from the beginning -->
      <xsl:variable name="bracketed"
           select="normalize-space(substring($href, 4))"/>
      <!-- Remove brackets -->
      <xsl:variable name="quoted"
           select="normalize-space(substring($bracketed, 2, string-length ($bracketed) - 2 ))"/>

      <xsl:variable name="q" select="'&quot;'"/>
      <xsl:variable name="a" select='"&apos;"'/>
      <!-- Remove optional quotes -->
      <xsl:choose>
        <xsl:when test="( substring($quoted, 1, 1) = $q and
                          substring($quoted, string-length($quoted), 1) = $q )
                     or ( substring($quoted, 1, 1) = $a and
                          substring($quoted, string-length($quoted), 1) = $a )">
          <xsl:value-of select="substring($quoted, 2, string-length($quoted) - 2)"/>
        </xsl:when>
        <xsl:otherwise><xsl:value-of select="$quoted"/></xsl:otherwise>
      </xsl:choose>
    </xsl:when>
    <xsl:otherwise><xsl:value-of select="."/></xsl:otherwise>
  </xsl:choose>
</xsl:template>

<!-- =============================================================== -->
<!-- Page number - replace by a bullet                               -->
<!-- =============================================================== -->

<xsl:template match="fo:page-number | fo:page-number-citation">
  <span>&add-style;<xsl:text>&#x2022;</xsl:text></span>
</xsl:template>

<!-- =============================================================== -->
<!-- Leader - replace by a space                                     -->
<!-- =============================================================== -->

<xsl:template match="fo:leader">
  <xsl:text> &#xA0;&#xA0;&#xA0; </xsl:text>
</xsl:template>



<!-- =============================================================== -->
<!-- Static content - add a <hr/> before or after it                 -->
<!-- =============================================================== -->

<xsl:template match="fo:flow | fo:static-content">
  <xsl:param name="region"/>

  <div>&add-style;&anchor;
    <xsl:apply-templates select="$region"/>
    <xsl:apply-templates/>
    <xsl:if test=".//fo:footnote">
      <br/><hr/>
      <xsl:apply-templates select=".//fo:footnote" mode="after-text"/>
    </xsl:if>
  </div>
</xsl:template>

<!-- =============================================================== -->
<!-- Footnotes                                                       -->
<!-- =============================================================== -->

<xsl:template match="fo:footnote">
  <xsl:apply-templates select="fo:inline"/>
</xsl:template>

<xsl:template match="fo:footnote" mode="after-text">
  <div>&add-style;&anchor;
    <xsl:apply-templates select="fo:footnote-body"/>
  </div>
</xsl:template>


<!-- =============================================================== -->
<!-- Copy form602onclick attribute-->
<!-- =============================================================== -->

<xsl:template name="add-onclick-attribute">    
  <xsl:if test="string-length(@form602onclick) &gt; 0">   
   <xsl:attribute name="onclick">perform_click_action(this)</xsl:attribute>
   <xsl:attribute name="zfo-app-event"><xsl:value-of select="@form602onclick"/></xsl:attribute>
  </xsl:if>
</xsl:template>


<!-- =============================================================== -->
<!-- Copy all CSS1-compatible attributes to "style" property         -->
<!-- =============================================================== -->

<xsl:template name="add-style-attribute">
  <xsl:param name="orientation" select="0"/>
  <xsl:variable name="style">
    <xsl:apply-templates select="@*" mode="collect-style-attributes"/>
  </xsl:variable>

  <xsl:if test="string-length($style) &gt; 0">
    <xsl:attribute name="style"><xsl:value-of select="normalize-space($style)"/></xsl:attribute>
  </xsl:if>
</xsl:template>

<!-- =============================================================== -->
<!-- Create an anchor                                                -->
<!-- =============================================================== -->

<xsl:template match="@id"><a name="{.}"/></xsl:template>

<!-- =============================================================== -->
<!-- Table cell geometry                                             -->
<!-- =============================================================== -->

<xsl:template match="@*" mode="get-table-attributes" priority="-1"/>

<xsl:template match="@number-columns-spanned"
                     mode="get-table-attributes">
  <xsl:attribute name="colspan"><xsl:value-of select="."/></xsl:attribute>
</xsl:template>

<xsl:template match="@number-rows-spanned"
                     mode="get-table-attributes">
  <xsl:attribute name="rowspan"><xsl:value-of select="."/></xsl:attribute>
</xsl:template>



<!-- =============================================================== -->
<!-- Page layout: determine master name for the first page           -->
<!-- =============================================================== -->

<xsl:template match="fo:page-sequence-master">
  <xsl:apply-templates select="*[1]"/>
</xsl:template>

<xsl:template match="fo:single-page-master-reference
                   | fo:repeatable-page-master-reference">
  <xsl:value-of select="@master-reference"/>
</xsl:template>

<xsl:template match="fo:repeatable-page-master-alternatives">
  <xsl:choose>
    <xsl:when test="fo:conditional-page-master-reference[@page-position='first']">
      <xsl:value-of select="fo:conditional-page-master-reference[@page-position='first'][1]/@master-reference"/>
    </xsl:when>
    <xsl:when test="fo:conditional-page-master-reference[@odd-or-even='odd' and not (@blank-or-not-blank='blank')]">
      <xsl:value-of select="fo:conditional-page-master-reference[@odd-or-even='odd' and not (@blank-or-not-blank='blank')][1]/@master-reference"/>
    </xsl:when>
    <xsl:when test="fo:conditional-page-master-reference[not(@odd-or-even='even') and not (@blank-or-not-blank='blank')]">
      <xsl:value-of select="fo:conditional-page-master-reference[not(@odd-or-even='even') and not (@blank-or-not-blank='blank')][1]/@master-reference"/>
    </xsl:when>
    <xsl:otherwise> <!-- cannot guess; take the first -->
      <xsl:value-of select="fo:conditional-page-master-reference[1]/@master-reference"/>
    </xsl:otherwise>
  </xsl:choose>
</xsl:template>


<!-- =============================================================== -->
<!-- Header/footer properties                                        -->
<!-- =============================================================== -->


<xsl:template match="@extent">
  <xsl:attribute name="width"><xsl:apply-templates select="." mode="convert-to-pixels"/></xsl:attribute>
</xsl:template>

<xsl:template match="@width | @height">
  <xsl:attribute name="{name()}"><xsl:apply-templates select="." mode="convert-to-pixels"/></xsl:attribute>
</xsl:template>

<xsl:template match="fo:region-before | fo:region-after">
  <xsl:call-template name="get-area-attributes"/>

  <!-- For header and footer, we suppress margins and padding -->
  <xsl:variable name="style">
    <xsl:apply-templates
          select="@*[not (starts-with (name(), 'margin')
                          or starts-with (name(), 'space')
                          or starts-with (name(), 'padding'))]"
          mode="collect-style-attributes">
      <xsl:with-param name="orientation" select="@reference-orientation"/>
    </xsl:apply-templates>
  </xsl:variable>

  <xsl:if test="string-length($style) &gt; 0">
    <xsl:attribute name="style"><xsl:value-of select="normalize-space($style)"/></xsl:attribute>
  </xsl:if>

</xsl:template>

<xsl:template match="fo:region-body">
  <xsl:call-template name="get-area-attributes"/>

  <!-- For region-body, we suppress margin attributes -->
  <xsl:variable name="style">
    <xsl:apply-templates
          select="@*[not (starts-with (name(), 'margin')
                          or starts-with (name(), 'space'))]"
          mode="collect-style-attributes">
      <xsl:with-param name="orientation" select="@reference-orientation"/>
    </xsl:apply-templates>
  </xsl:variable>

  <xsl:if test="string-length($style) &gt; 0">
    <xsl:attribute name="style"><xsl:value-of select="normalize-space($style)"/></xsl:attribute>
  </xsl:if>
</xsl:template>

<xsl:template match="fo:region-start | fo:region-end"/>

<xsl:template name="get-area-attributes">
  <xsl:attribute name="valign">
    <xsl:choose>
      <xsl:when test="@display-align"><xsl:value-of select="@display-align"/></xsl:when>
      <xsl:otherwise>top</xsl:otherwise>
    </xsl:choose>
  </xsl:attribute>
</xsl:template>

</xsl:stylesheet>
