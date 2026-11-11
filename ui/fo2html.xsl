<?xml version="1.0" encoding="UTF-8"?>
<!-- Corrections on top of zfo-editor's FO→HTML stylesheet; templates here take precedence over the imported ones. -->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:fo="http://www.w3.org/1999/XSL/Format"
                xmlns:fm="http://software602.cz/forms"
                xmlns:bl="urn:blanketa"
                exclude-result-prefixes="fo fm bl">

  <xsl:import href="../vendor/zfo-editor-src/zfo-editor-0.3.2/xslt/fo2html.xsl"/>

  <!-- A check box carries the values it writes: several boxes of one bind with their own values make one choice. -->
  <!-- Some forms name a radio button or check box only by its bind. zfo-editor reads form602radio and form602chkbx
       alone. A radio button's empty name then stops the page at the selector "#" while it loads the values,
       and a check box without a name silently gets none. -->
  <xsl:template name="radio-input">
    <xsl:variable name="name" select="(@form602radio[. != ''] | @form602bind[not(../@form602radio != '')])"/>
    <input title="{@form602tooltip}" type="radio" name="{$name}" id="{concat($name, '-', @form602on)}" value="{@form602on}">
      <xsl:apply-templates/>
    </input>
  </xsl:template>

  <xsl:template name="checkbox-input">
    <xsl:variable name="name" select="(@form602chkbx[. != ''] | @form602bind[not(../@form602chkbx != '')])"/>
    <input title="{@form602tooltip}" type="checkbox" id="{$name}" name="{$name}">
      <xsl:if test="@form602on"><xsl:attribute name="value"><xsl:value-of select="@form602on"/></xsl:attribute></xsl:if>
      <xsl:if test="@form602off"><xsl:attribute name="data-off"><xsl:value-of select="@form602off"/></xsl:attribute></xsl:if>
      <xsl:apply-templates/>
    </input>
  </xsl:template>

  <!-- Some forms download a code list when they open; one Blanketa could not get (codelists.py) stays empty. The field stays
       fillable by hand: as an empty list it could never be filled, and a required one would stop the form at each check. -->
  <xsl:template name="create-select">
    <xsl:param name="list-name"/>
    <xsl:param name="list-bindname"/>
    <xsl:param name="tooltip"/>
    <xsl:variable name="items" select="//fm:enum[@name=$list-name]/fm:list/fm:listitem"/>
    <xsl:choose>
      <xsl:when test="$items">
        <select title="{$tooltip}" style="width: 100%;" name="{@form602bind}">
          <xsl:call-template name="draw-list">
            <xsl:with-param name="items" select="$items"/>
          </xsl:call-template>
        </select>
      </xsl:when>
      <xsl:otherwise>
        <input type="text" title="{$tooltip}" style="width: 100%;" name="{@form602bind}"/>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- A whisperer offers the items of a list Blanketa got for it (codelists.py); the list goes onto the page once. -->
  <xsl:template match="fo:inline[@form602ct='acm']">
    <input type="text" title="{@form602tooltip}" style="width: 100%;" name="{@form602bind}">
      <xsl:if test="@blanketa-list">
        <xsl:attribute name="list"><xsl:value-of select="@blanketa-list"/></xsl:attribute>
      </xsl:if>
    </input>
    <xsl:if test="@blanketa-list and not(preceding::fo:inline[@form602ct='acm'][@blanketa-list = current()/@blanketa-list])">
      <datalist id="{@blanketa-list}">
        <xsl:for-each select="//fm:enum[@name = current()/@blanketa-list]/fm:list/fm:listitem">
          <option value="{@itemtext}"/>
        </xsl:for-each>
      </datalist>
    </xsl:if>
  </xsl:template>

  <!-- Dates, times, numbers and outputs carry their picture (form602format) for the page to show the stored value by it.
       A date can be typed as well as picked: zfo-editor only let it be picked, in the stored form. -->
  <xsl:template name="date-input">
    <input type="text" class="date-pick" title="{@form602tooltip}" name="{@form602bind}" data-format="{@form602format}"/>
  </xsl:template>

  <!-- No maxlength: the field shows the number with the spaces and decimals of its picture, while form602maxchars counts
       the stored value, which the form check measures. -->
  <xsl:template name="num-input">
    <input type="text" class="numberInput" title="{@form602tooltip}" name="{@form602bind}" data-format="{@form602format}"/>
  </xsl:template>

  <!-- zfo-editor draws no field for a time at all. -->
  <xsl:template name="time-input">
    <input type="text" class="timeInput" title="{@form602tooltip}" maxlength="{@form602maxchars}" name="{@form602bind}"
           data-format="{@form602format}"/>
  </xsl:template>

  <xsl:template match="fo:table-cell[@form602ct='tim']" mode="display">
    <td>
      <xsl:call-template name="add-style-attribute"/>
      <xsl:call-template name="add-onclick-attribute"/>
      <xsl:apply-templates select="@*" mode="get-table-attributes"/>
      <xsl:call-template name="time-input"/>
    </td>
  </xsl:template>

  <xsl:template match="fo:block[@form602ct='tim']|fo:block-container[@form602ct='tim']">
    <div>
      <xsl:call-template name="add-style-attribute"/>
      <xsl:call-template name="add-onclick-attribute"/>
      <xsl:call-template name="time-input"/>
    </div>
  </xsl:template>

  <xsl:template match="fo:inline[@form602ct='tim']">
    <span>
      <xsl:call-template name="add-style-attribute"/>
      <xsl:call-template name="add-onclick-attribute"/>
      <xsl:call-template name="time-input"/>
    </span>
  </xsl:template>

  <xsl:template match="fo:block[@form602ct='out']">
    <div>
      <xsl:call-template name="add-style-attribute"/>
      <xsl:call-template name="add-onclick-attribute"/>
      <xsl:call-template name="output-attributes"/>
      <xsl:apply-templates mode="check-for-pre"/>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="fo:inline[@form602ct='out']">
    <span>
      <xsl:call-template name="add-style-attribute"/>
      <xsl:call-template name="add-onclick-attribute"/>
      <xsl:call-template name="output-attributes"/>
    </span>
  </xsl:template>

  <xsl:template name="output-attributes">
    <xsl:attribute name="id"><xsl:value-of select="@form602bindref"/></xsl:attribute>
    <xsl:attribute name="class">outputelement</xsl:attribute>
    <xsl:attribute name="name"><xsl:value-of select="@form602bindref"/></xsl:attribute>
    <xsl:if test="@form602format">
      <xsl:attribute name="data-format"><xsl:value-of select="@form602format"/></xsl:attribute>
    </xsl:if>
  </xsl:template>

  <!-- zfo-editor's own template, and the part of the form a block opens: form602anchor names it in Form Filler's navigation
       (NavigationDlg). Every block, cell and inline passes here. -->
  <xsl:template name="add-onclick-attribute">
    <xsl:if test="string-length(@form602onclick) &gt; 0">
      <xsl:attribute name="onclick">perform_click_action(this)</xsl:attribute>
      <xsl:attribute name="zfo-app-event"><xsl:value-of select="@form602onclick"/></xsl:attribute>
    </xsl:if>
    <xsl:if test="normalize-space(@form602anchor) != ''">
      <xsl:attribute name="data-anchor"><xsl:value-of select="normalize-space(@form602anchor)"/></xsl:attribute>
    </xsl:if>
  </xsl:template>

  <!-- A button with a script names itself by its position: the script stays on the server and runs from there. -->
  <xsl:template name="button-input">
    <button type="button" title="{@form602tooltip}" zfo-app-event="{@form602btnclick}">
      <xsl:choose>
        <xsl:when test="@blanketa-button">
          <xsl:attribute name="onclick">run_form_button(this)</xsl:attribute>
          <xsl:attribute name="data-form-button"><xsl:value-of select="@blanketa-button"/></xsl:attribute>
        </xsl:when>
        <xsl:otherwise>
          <xsl:attribute name="onclick">perform_click_action(this)</xsl:attribute>
          <xsl:attribute name="value"><xsl:value-of select="@form602btnclick"/></xsl:attribute>
        </xsl:otherwise>
      </xsl:choose>
      <xsl:apply-templates/>
    </button>
  </xsl:template>

  <xsl:template match="fo:instream-foreign-object[@blanketa-button]">
    <button type="button" class="zfo-picture-button" title="{@form602tooltip}" zfo-app-event="{@form602btnclick}"
            onclick="run_form_button(this)" data-form-button="{@blanketa-button}">
      <xsl:apply-templates/>
    </button>
  </xsl:template>

  <!-- A section named only by its bind has buttons of the form's own, the page's + and - would double them. -->
  <xsl:template match="fo:block[@blanketa-section-by-bind]">
    <div name="div-rep-container">
      <div name="{@form602repe}-rep-0" class="602-repetition-block zfo-section-with-own-buttons">
        <button onclick="clone_div(this.parentNode)" type="button">+</button>
        <button onclick="del_div(this.parentNode)" type="button">-</button>
        <br/>
        <xsl:apply-templates mode="check-for-pre"/>
      </div>
    </div>
  </xsl:template>

  <!-- Barcode the form draws itself (zfo-editor drops it): an SVG from barcode.py; its text when it cannot be drawn. -->
  <xsl:template match="fm:barcode">
    <xsl:variable name="src" select="bl:barcode(string(@symbology), normalize-space(.), string(@barcode-height),
                                                string(@narrow-bar-width), string(@display-text))"/>
    <xsl:choose>
      <xsl:when test="$src != ''"><img class="zfo-barcode" src="{$src}" alt="Čárový kód {normalize-space(.)}"/></xsl:when>
      <xsl:otherwise><xsl:value-of select="normalize-space(.)"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- Embedded picture: real image type from its signature, size from the enclosing fo:instream-foreign-object. -->
  <xsl:template match="fm:internal-graphic">
    <xsl:variable name="data" select="normalize-space(text())"/>
    <xsl:variable name="type">
      <xsl:choose>
        <xsl:when test="starts-with($data, 'iVBOR')">png</xsl:when>
        <xsl:when test="starts-with($data, 'R0lG')">gif</xsl:when>
        <xsl:when test="starts-with($data, 'Qk')">bmp</xsl:when>
        <xsl:otherwise>jpeg</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:variable name="box" select="ancestor::fo:instream-foreign-object[1]"/>
    <img src="data:image/{$type};base64,{$data}" alt="">
      <xsl:attribute name="style">
        <xsl:text>display: inline-block; max-width: 100%; object-fit: contain;</xsl:text>
        <xsl:if test="$box/@width"> width: <xsl:value-of select="$box/@width"/>;</xsl:if>
        <xsl:if test="$box/@height"> height: <xsl:value-of select="$box/@height"/>;</xsl:if>
      </xsl:attribute>
    </img>
  </xsl:template>

  <!-- Some forms draw all their text and lines as one picture behind the region body, stored as a bare base64 string
       (zfo-editor puts it on <body>, where no browser loads it). It goes onto the region body as a data URI, scaled
       to its width; flow-root keeps the first block's margin inside, so the fields stay where the picture has them. -->
  <xsl:template match="fo:region-body/@background-image" mode="collect-style-attributes">
    <xsl:variable name="raw" select="normalize-space(.)"/>
    <xsl:variable name="unwrapped">
      <xsl:choose>
        <xsl:when test="starts-with($raw, 'url(')">
          <xsl:value-of select="translate(substring($raw, 5, string-length($raw) - 5), &quot;'&quot;, '')"/>
        </xsl:when>
        <xsl:otherwise><xsl:value-of select="$raw"/></xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:variable name="data" select="translate($unwrapped, '&quot;', '')"/>
    <xsl:variable name="type">
      <xsl:choose>
        <xsl:when test="starts-with($data, 'iVBOR')">png</xsl:when>
        <xsl:when test="starts-with($data, 'R0lG')">gif</xsl:when>
        <xsl:when test="starts-with($data, 'Qk')">bmp</xsl:when>
        <xsl:otherwise>jpeg</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:text>background-image: url(</xsl:text>
    <xsl:choose>
      <xsl:when test="starts-with($data, 'data:') or starts-with($data, 'http')"><xsl:value-of select="$data"/></xsl:when>
      <xsl:otherwise>data:image/<xsl:value-of select="$type"/>;base64,<xsl:value-of select="$data"/></xsl:otherwise>
    </xsl:choose>
    <xsl:text>); background-size: 100% auto; display: flow-root; </xsl:text>
  </xsl:template>

</xsl:stylesheet>
